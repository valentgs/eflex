from __future__ import annotations

import copy
import json

from flask import url_for, current_app, request
from flask_classful import FlaskView, route
from flask_wtf import FlaskForm
from flask_security import login_required, current_user
from webargs.flaskparser import use_kwargs
from wtforms import StringField, DecimalField, SelectField
from wtforms.validators import DataRequired, optional
from sqlalchemy import select
from flexmeasures.auth.policy import user_has_admin_access

from flexmeasures.data import db
from flexmeasures.auth.error_handling import unauthorized_handler
from flexmeasures.auth.policy import check_access
from flexmeasures.data.schemas import StartEndTimeSchema
from flexmeasures.data.services.job_cache import NoRedisConfigured
from flexmeasures.data.models.generic_assets import (
    GenericAssetType,
    GenericAsset,
    get_center_location_of_assets,
)
from flexmeasures.data.models.user import Account
from flexmeasures.data.models.time_series import Sensor
from flexmeasures.ui.utils.view_utils import render_flexmeasures_template
from flexmeasures.ui.crud.api_wrapper import InternalApi
from flexmeasures.data.services.sensors import (
    build_sensor_status_data,
    build_asset_jobs_data,
)


"""
Asset crud view.

Note: This uses the internal dev API version
      ― if those endpoints get moved or updated to a higher version,
      we probably should change the version used here, as well.
"""


class AssetForm(FlaskForm):
    """The default asset form only allows to edit the name and location."""

    name = StringField("Name")
    latitude = DecimalField(
        "Latitude",
        validators=[optional()],
        places=None,
        render_kw={"placeholder": "--Click the map or enter a latitude--"},
    )
    longitude = DecimalField(
        "Longitude",
        validators=[optional()],
        places=None,
        render_kw={"placeholder": "--Click the map or enter a longitude--"},
    )
    attributes = StringField("Other attributes (JSON)", default="{}")

    def validate_on_submit(self):
        if (
            hasattr(self, "generic_asset_type_id")
            and self.generic_asset_type_id.data == -1
        ):
            self.generic_asset_type_id.data = (
                ""  # cannot be coerced to int so will be flagged as invalid input
            )
        if hasattr(self, "account_id") and self.account_id.data == -1:
            del self.account_id  # asset will be public
        return super().validate_on_submit()

    def to_json(self) -> dict:
        """turn form data into a JSON we can POST to our internal API"""
        data = copy.copy(self.data)
        if data.get("longitude") is not None:
            data["longitude"] = float(data["longitude"])
        if data.get("latitude") is not None:
            data["latitude"] = float(data["latitude"])

        if "csrf_token" in data:
            del data["csrf_token"]

        return data

    def process_api_validation_errors(self, api_response: dict):
        """Process form errors from the API for the WTForm"""
        if not isinstance(api_response, dict):
            return
        for error_header in ("json", "validation_errors"):
            if error_header not in api_response:
                continue
            for field in list(self._fields.keys()):
                if field in list(api_response[error_header].keys()):
                    field_errors = api_response[error_header][field]
                    if isinstance(field_errors, list):
                        self._fields[field].errors += api_response[error_header][field]
                    else:
                        self._fields[field].errors.append(
                            api_response[error_header][field]
                        )


class NewAssetForm(AssetForm):
    """Here, in addition, we allow to set asset type and account."""

    generic_asset_type_id = SelectField(
        "Asset type", coerce=int, validators=[DataRequired()]
    )
    account_id = SelectField("Account", coerce=int)


def with_options(form: AssetForm | NewAssetForm) -> AssetForm | NewAssetForm:
    if "generic_asset_type_id" in form:
        form.generic_asset_type_id.choices = [(-1, "--Select type--")] + [
            (atype.id, atype.name)
            for atype in db.session.scalars(select(GenericAssetType)).all()
        ]
    if "account_id" in form:
        form.account_id.choices = [(-1, "--Select account--")] + [
            (account.id, account.name)
            for account in db.session.scalars(select(Account)).all()
        ]
    return form


def process_internal_api_response(
    asset_data: dict, asset_id: int | None = None, make_obj=False
) -> GenericAsset | dict:
    """
    Turn data from the internal API into something we can use to further populate the UI.
    Either as an asset object or a dict for form filling.

    If we add other data by querying the database, we make sure the asset is not in the session afterwards.
    """

    def expunge_asset():
        # use if no insert is wanted from a previous query which flushes its results
        if asset in db.session:
            db.session.expunge(asset)

    asset_data.pop("status", None)  # might have come from requests.response
    if asset_id:
        asset_data["id"] = asset_id
    if make_obj:
        children = asset_data.pop("child_assets", [])

        asset = GenericAsset(
            **{
                **asset_data,
                **{"attributes": json.loads(asset_data.get("attributes", "{}"))},
            }
        )  # TODO: use schema?
        asset.generic_asset_type = db.session.get(
            GenericAssetType, asset.generic_asset_type_id
        )
        expunge_asset()
        asset.owner = db.session.get(Account, asset_data["account_id"])
        expunge_asset()
        db.session.flush()
        if "id" in asset_data:
            asset.sensors = db.session.scalars(
                select(Sensor).filter_by(generic_asset_id=asset_data["id"])
            ).all()
            expunge_asset()
        if asset_data.get("parent_asset_id", None) is not None:
            asset.parent_asset = db.session.execute(
                select(GenericAsset).filter(
                    GenericAsset.id == asset_data["parent_asset_id"]
                )
            ).scalar_one_or_none()
            expunge_asset()

        child_assets = []
        for child in children:
            child.pop("child_assets")
            child_asset = process_internal_api_response(child, child["id"], True)
            child_assets.append(child_asset)
        asset.child_assets = child_assets
        expunge_asset()

        return asset
    return asset_data


def user_can_create_assets() -> bool:
    try:
        check_access(current_user.account, "create-children")
    except Exception:
        return False
    return True


def user_can_delete(asset) -> bool:
    try:
        check_access(asset, "delete")
    except Exception:
        return False
    return True


def get_assets_by_account(account_id: int | str | None) -> list[GenericAsset]:
    if account_id is not None:
        get_assets_response = InternalApi().get(
            url_for("AssetAPI:index"), query={"account_id": account_id}
        )
    else:
        get_assets_response = InternalApi().get(url_for("AssetAPI:public"))
    return [
        process_internal_api_response(ad, make_obj=True)
        for ad in get_assets_response.json()
    ]


class AssetCrudUI(FlaskView):
    """
    These views help us offer a Jinja2-based UI.
    The main focus on logic is the API, so these views simply call the API functions,
    and deal with the response.
    Some new functionality, like fetching accounts and asset types, is added here.
    """

    route_base = "/assets"
    trailing_slash = False

    @login_required
    def index(self, msg=""):
        """GET from /assets

        List the user's assets. For admins, list across all accounts.
        """
        assets = []

        if user_has_admin_access(current_user, "read"):
            for account in db.session.scalars(select(Account)).all():
                assets += get_assets_by_account(account.id)
            assets += get_assets_by_account(account_id=None)
        else:
            assets = get_assets_by_account(current_user.account_id)

        return render_flexmeasures_template(
            "crud/assets.html",
            assets=assets,
            message=msg,
            user_can_create_assets=user_can_create_assets(),
        )

    @login_required
    def owned_by(self, account_id: str):
        """/assets/owned_by/<account_id>"""
        msg = ""
        get_assets_response = InternalApi().get(
            url_for("AssetAPI:index"),
            query={"account_id": account_id},
            do_not_raise_for=[404],
        )
        if get_assets_response.status_code == 404:
            assets = []
            msg = f"Account {account_id} unknown."
        else:
            assets = [
                process_internal_api_response(ad, make_obj=True)
                for ad in get_assets_response.json()
            ]
        db.session.flush()
        return render_flexmeasures_template(
            "crud/assets.html",
            account=db.session.get(Account, account_id),
            assets=assets,
            msg=msg,
            user_can_create_assets=user_can_create_assets(),
        )

    @use_kwargs(StartEndTimeSchema, location="query")
    @login_required
    def get(self, id: str, **kwargs):
        """GET from /assets/<id> where id can be 'new' (and thus the form for asset creation is shown)
        The following query parameters are supported (should be used only together):
         - start_time: minimum time of the events to be shown
         - end_time: maximum time of the events to be shown
        """
        if id == "new":
            if not user_can_create_assets():
                return unauthorized_handler(None, [])

            asset_form = with_options(NewAssetForm())
            return render_flexmeasures_template(
                "crud/asset_new.html",
                asset_form=asset_form,
                msg="",
                map_center=get_center_location_of_assets(user=current_user),
                mapboxAccessToken=current_app.config.get("MAPBOX_ACCESS_TOKEN", ""),
            )

        get_asset_response = InternalApi().get(url_for("AssetAPI:fetch_one", id=id))
        asset_dict = get_asset_response.json()

        asset_form = with_options(AssetForm())

        asset = process_internal_api_response(asset_dict, int(id), make_obj=True)
        asset_form.process(data=process_internal_api_response(asset_dict))

        return render_flexmeasures_template(
            "crud/asset.html",
            asset=asset,
            asset_form=asset_form,
            msg="",
            mapboxAccessToken=current_app.config.get("MAPBOX_ACCESS_TOKEN", ""),
            user_can_create_assets=user_can_create_assets(),
            user_can_delete_asset=user_can_delete(asset),
            event_starts_after=request.args.get("start_time"),
            event_ends_before=request.args.get("end_time"),
        )

    @login_required
    @route("/<id>/status")
    def status(self, id: str):
        """GET from /assets/<id>/status to show the staleness of the asset's sensors."""

        get_asset_response = InternalApi().get(url_for("AssetAPI:fetch_one", id=id))
        asset_dict = get_asset_response.json()

        asset = process_internal_api_response(asset_dict, int(id), make_obj=True)
        status_data = build_sensor_status_data(asset)

        # add data about forecasting and scheduling jobs
        redis_connection_err = None
        scheduling_job_data, forecasting_job_data = list(), list()
        try:
            jobs_data = build_asset_jobs_data(asset)
        except NoRedisConfigured as e:
            redis_connection_err = e.args[0]
        else:
            scheduling_job_data = [
                jd for jd in jobs_data if jd["queue"] == "scheduling"
            ]
            forecasting_job_data = [
                jd for jd in jobs_data if jd["queue"] == "forecasting"
            ]

        return render_flexmeasures_template(
            "views/status.html",
            asset=asset,
            sensors=status_data,
            scheduling_job_data=scheduling_job_data,
            forecasting_job_data=forecasting_job_data,
            redis_connection_err=redis_connection_err,
        )

    @login_required
    def post(self, id: str):
        """POST to /assets/<id>, where id can be 'create' (and thus a new asset is made from POST data)
        Most of the code deals with creating a user for the asset if no existing is chosen.
        """
        print("BBBBBBBBBBBBBBBBBBBBBBBB")
        asset: GenericAsset = None
        error_msg = ""

        if id == "create":
            asset_form = with_options(NewAssetForm())

            account, account_error = _set_account(asset_form)
            asset_type, asset_type_error = _set_asset_type(asset_form)

            form_valid = asset_form.validate_on_submit()

            # Fill up the form with useful errors for the user
            if account_error is not None:
                form_valid = False
                asset_form.account_id.errors.append(account_error)
            if asset_type_error is not None:
                form_valid = False
                asset_form.generic_asset_type_id.errors.append(asset_type_error)

            # Create new asset or return the form for new assets with a message
            if form_valid and asset_type is not None:
                post_asset_response = InternalApi().post(
                    url_for("AssetAPI:post"),
                    args=asset_form.to_json(),
                    do_not_raise_for=[400, 422],
                )
                if post_asset_response.status_code in (200, 201):
                    asset_dict = post_asset_response.json()
                    asset = process_internal_api_response(
                        asset_dict, int(asset_dict["id"]), make_obj=True
                    )
                    msg = "Creation was successful."
                else:
                    current_app.logger.error(
                        f"Internal asset API call unsuccessful [{post_asset_response.status_code}]: {post_asset_response.text}"
                    )
                    asset_form.process_api_validation_errors(post_asset_response.json())
                    if "message" in post_asset_response.json():
                        asset_form.process_api_validation_errors(
                            post_asset_response.json()["message"]
                        )
                        if "json" in post_asset_response.json()["message"]:
                            error_msg = str(
                                post_asset_response.json()["message"]["json"]
                            )
            if asset is None:
                msg = "Cannot create asset. " + error_msg
                return render_flexmeasures_template(
                    "crud/asset_new.html",
                    asset_form=asset_form,
                    msg=msg,
                    map_center=get_center_location_of_assets(user=current_user),
                    mapboxAccessToken=current_app.config.get("MAPBOX_ACCESS_TOKEN", ""),
                )

        else:
            asset_form = with_options(AssetForm())
            if not asset_form.validate_on_submit():
                asset = db.session.get(GenericAsset, id)
                # Display the form data, but set some extra data which the page wants to show.
                asset_info = asset_form.to_json()
                asset_info["id"] = id
                asset_info["account_id"] = asset.account_id
                asset = process_internal_api_response(
                    asset_info, int(id), make_obj=True
                )

                return render_flexmeasures_template(
                    "crud/asset.html",
                    asset_form=asset_form,
                    asset=asset,
                    msg="Cannot edit asset.",
                    mapboxAccessToken=current_app.config.get("MAPBOX_ACCESS_TOKEN", ""),
                    user_can_create_assets=user_can_create_assets(),
                    user_can_delete_asset=user_can_delete(asset),
                )
            patch_asset_response = InternalApi().patch(
                url_for("AssetAPI:patch", id=id),
                args=asset_form.to_json(),
                do_not_raise_for=[400, 422],
            )
            asset_dict = patch_asset_response.json()
            if patch_asset_response.status_code in (200, 201):
                asset = process_internal_api_response(
                    asset_dict, int(id), make_obj=True
                )
                msg = "Editing was successful."
            else:
                current_app.logger.error(
                    f"Internal asset API call unsuccessful [{patch_asset_response.status_code}]: {patch_asset_response.text}"
                )
                msg = "Cannot edit asset."
                asset_form.process_api_validation_errors(
                    patch_asset_response.json().get("message")
                )
                asset = db.session.get(GenericAsset, id)

        return render_flexmeasures_template(
            "crud/asset.html",
            asset=asset,
            asset_form=asset_form,
            msg=msg,
            mapboxAccessToken=current_app.config.get("MAPBOX_ACCESS_TOKEN", ""),
            user_can_create_assets=user_can_create_assets(),
            user_can_delete_asset=user_can_delete(asset),
        )

    @login_required
    def delete_with_data(self, id: str):
        """Delete via /assets/delete_with_data/<id>"""
        InternalApi().delete(url_for("AssetAPI:delete", id=id))
        return self.index(
            msg=f"Asset {id} and assorted meter readings / forecasts have been deleted."
        )


def _set_account(asset_form: NewAssetForm) -> tuple[Account | None, str | None]:
    """Set an account for the to-be-created asset.
    Return the account (if available) and an error message"""
    account_error = None

    if asset_form.account_id.data == -1:
        if user_has_admin_access(current_user, "update"):
            return None, None  # Account can be None (public asset)
        else:
            account_error = "Please pick an existing account."

    account = db.session.execute(
        select(Account).filter_by(id=int(asset_form.account_id.data))
    ).scalar_one_or_none()

    if account:
        asset_form.account_id.data = account.id
    else:
        current_app.logger.error(account_error)
    return account, account_error


def _set_asset_type(
    asset_form: NewAssetForm,
) -> tuple[GenericAssetType | None, str | None]:
    """Set an asset type for the to-be-created asset.
    Return the asset type (if available) and an error message."""
    asset_type = None
    asset_type_error = None

    if int(asset_form.generic_asset_type_id.data) == -1:
        asset_type_error = "Pick an existing asset type."
    else:
        asset_type = db.session.execute(
            select(GenericAssetType).filter_by(
                id=int(asset_form.generic_asset_type_id.data)
            )
        ).scalar_one_or_none()

    if asset_type:
        asset_form.generic_asset_type_id.data = asset_type.id
    else:
        current_app.logger.error(asset_type_error)
    return asset_type, asset_type_error
