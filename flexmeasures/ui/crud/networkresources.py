from __future__ import annotations

import copy
import json

from flask import url_for, current_app, request
from flask_classful import FlaskView, route
from flask_wtf import FlaskForm
from flask_security import login_required, current_user
import flexmeasures
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
from flexmeasures.data.models.network_resources import (
    NetworkResourceType,
    NetworkResource,
)
from flexmeasures.data.models.user import Account
from flexmeasures.data.models.time_series import Sensor
from flexmeasures.ui.utils.view_utils import render_flexmeasures_template
from flexmeasures.ui.crud.api_wrapper import InternalApi
from flexmeasures.data.services.sensors import (
    build_sensor_status_data,
    build_asset_jobs_data,
)

from flexmeasures.api.v3_0.network_resources import NetworkResourceAPI


"""
Network Resource crud view.

Note: This uses the internal dev API version
      ― if those endpoints get moved or updated to a higher version,
      we probably should change the version used here, as well.
"""


class NetworkResourceForm(FlaskForm):
    """The default network resource form only allows to edit the name and location."""

    name = StringField("Name")
    attributes = StringField("Other attributes (JSON)", default="{}")

    def validate_on_submit(self):
        if (
            hasattr(self, "network_resource_type_id")
            and self.network_resource_type_id.data == -1
        ):
            self.network_resource_type_id.data = (
                ""  # cannot be coerced to int so will be flagged as invalid input
            )
        if hasattr(self, "account_id") and self.account_id.data == -1:
            del self.account_id  # asset will be public
        return super().validate_on_submit()

    def to_json(self) -> dict:
        """turn form data into a JSON we can POST to our internal API"""
        data = copy.copy(self.data)

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


class NewNetworkResourceForm(NetworkResourceForm):
    """Here, in addition, we allow to set network resource type and account."""

    network_resource_type_id = SelectField(
        "Network resource type", coerce=int, validators=[DataRequired()]
    )
    account_id = SelectField("Account", coerce=int)


def with_options(form: NetworkResourceForm | NewNetworkResourceForm) -> NetworkResourceForm | NewNetworkResourceForm:
    if "network_resource_type_id" in form:
        form.network_resource_type_id.choices = [(-1, "--Select type--")] + [
            (atype.id, atype.name)
            for atype in db.session.scalars(select(NetworkResourceType)).all()
        ]
    if "account_id" in form:
        form.account_id.choices = [(-1, "--Select account--")] + [
            (account.id, account.name)
            for account in db.session.scalars(select(Account)).all()
        ]
    return form


def process_internal_api_response(
    network_resource_data: dict, network_resource_id: int | None = None, make_obj=False
) -> NetworkResource | dict:
    """
    Turn data from the internal API into something we can use to further populate the UI.
    Either as an network resource object or a dict for form filling.

    If we add other data by querying the database, we make sure the network resource is not in the session afterwards.
    """

    def expunge_network_resource():
        # use if no insert is wanted from a previous query which flushes its results
        if network_resource in db.session:
            db.session.expunge(network_resource)

    network_resource_data.pop("status", None)  # might have come from requests.response
    if network_resource_id:
        network_resource_data["id"] = network_resource_id

    if make_obj:

        network_resource = NetworkResource(
            **{
                **network_resource_data,
                **{"attributes": json.loads(network_resource_data.get("attributes", "{}"))},
            }
        )  # TODO: use schema?

        network_resource.network_resource_type = db.session.get(
            NetworkResourceType, network_resource.network_resource_type_id
        )

        expunge_network_resource()
        network_resource.owner = db.session.get(Account, network_resource_data["account_id"])
        expunge_network_resource()
        db.session.flush()


        return network_resource
    return network_resource_data


def user_can_create_network_resources() -> bool:
    try:
        check_access(current_user.account, "create-children")
    except Exception:
        return False
    return True


def user_can_delete(network_resource) -> bool:
    try:
        check_access(network_resource, "delete")
    except Exception:
        return False
    return True


def get_network_resources_by_account(account_id: int | str | None) -> list[NetworkResource]:
    if account_id is not None:
        get_network_resources_response = InternalApi().get(
            url_for("NetworkResourceAPI:index"), query={"account_id": account_id}
        )
    else:
        get_network_resources_response = InternalApi().get(url_for("NetworkResourceAPI:public"))
    
    return [
        process_internal_api_response(ad, make_obj=True)
        for ad in get_network_resources_response.json()
    ]


class NetworkResourceCrudUI(FlaskView):
    """
    These views help us offer a Jinja2-based UI.
    The main focus on logic is the API, so these views simply call the API functions,
    and deal with the response.
    Some new functionality, like fetching accounts and network resource types, is added here.
    """

    route_base = "/networkresources"
    trailing_slash = False

    @login_required
    def index(self, msg=""):
        """GET from /networkresources

        List the user's network resources. For admins, list across all accounts.
        """
        network_resources = []

        if user_has_admin_access(current_user, "read"):
            for account in db.session.scalars(select(Account)).all():
                network_resources += get_network_resources_by_account(account.id)
            network_resources += get_network_resources_by_account(account_id=None)
        else:
            network_resources = get_network_resources_by_account(current_user.account_id)
        
        return render_flexmeasures_template(
            "crud/networkresources.html",
            network_resources=network_resources,
            message=msg,
            user_can_create_network_resources=user_can_create_network_resources(),
        )

#     @login_required
#     def owned_by(self, account_id: str):
#         """/assets/owned_by/<account_id>"""
#         msg = ""
#         get_assets_response = InternalApi().get(
#             url_for("AssetAPI:index"),
#             query={"account_id": account_id},
#             do_not_raise_for=[404],
#         )
#         if get_assets_response.status_code == 404:
#             assets = []
#             msg = f"Account {account_id} unknown."
#         else:
#             assets = [
#                 process_internal_api_response(ad, make_obj=True)
#                 for ad in get_assets_response.json()
#             ]
#         db.session.flush()
#         return render_flexmeasures_template(
#             "crud/assets.html",
#             account=db.session.get(Account, account_id),
#             assets=assets,
#             msg=msg,
#             user_can_create_assets=user_can_create_assets(),
#         )

    @use_kwargs(StartEndTimeSchema, location="query")
    @login_required
    def get(self, id: str, **kwargs):
        """GET from /networkresources/<id> where id can be 'new' (and thus the form for networkresource creation is shown)
        The following query parameters are supported (should be used only together):
         - start_time: minimum time of the events to be shown
         - end_time: maximum time of the events to be shown
        """
        if id == "new":
            if not user_can_create_network_resources():
                return unauthorized_handler(None, [])

            network_resource_form = with_options(NewNetworkResourceForm())
            return render_flexmeasures_template(
                "crud/networkresource_new.html",
                network_resource_form=network_resource_form,
                msg="",
                mapboxAccessToken=current_app.config.get("MAPBOX_ACCESS_TOKEN", ""),
            )
        get_network_resource_response = InternalApi().get(url_for("NetworkResourceAPI:fetch_one", id=id))

        network_resource_dict = get_network_resource_response.json()
        
        network_resource_form = with_options(NetworkResourceForm())

        network_resource = process_internal_api_response(network_resource_dict, int(id), make_obj=True)

        network_resource_form.process(data=process_internal_api_response(network_resource_dict))

        return render_flexmeasures_template(
            "crud/networkresource.html",
            network_resource=network_resource,
            network_resource_form=network_resource_form,
            msg="",
            user_can_create_network_resources=user_can_create_network_resources(),
            user_can_delete_network_resource=user_can_delete(network_resource)
        )

#     @login_required
#     @route("/<id>/status")
#     def status(self, id: str):
#         """GET from /assets/<id>/status to show the staleness of the asset's sensors."""

#         get_asset_response = InternalApi().get(url_for("AssetAPI:fetch_one", id=id))
#         asset_dict = get_asset_response.json()

#         asset = process_internal_api_response(asset_dict, int(id), make_obj=True)
#         status_data = build_sensor_status_data(asset)

#         # add data about forecasting and scheduling jobs
#         redis_connection_err = None
#         scheduling_job_data, forecasting_job_data = list(), list()
#         try:
#             jobs_data = build_asset_jobs_data(asset)
#         except NoRedisConfigured as e:
#             redis_connection_err = e.args[0]
#         else:
#             scheduling_job_data = [
#                 jd for jd in jobs_data if jd["queue"] == "scheduling"
#             ]
#             forecasting_job_data = [
#                 jd for jd in jobs_data if jd["queue"] == "forecasting"
#             ]

#         return render_flexmeasures_template(
#             "views/status.html",
#             asset=asset,
#             sensors=status_data,
#             scheduling_job_data=scheduling_job_data,
#             forecasting_job_data=forecasting_job_data,
#             redis_connection_err=redis_connection_err,
#         )

    @login_required
    def post(self, id: str):
        """POST to /networkresources/<id>, where id can be 'create' (and thus a new network resource is made from POST data)
        Most of the code deals with creating a user for the network resource if no existing is chosen.
        """
        network_resource: NetworkResource = None
        error_msg = ""
        if id == "create":
            network_resource_form = with_options(NewNetworkResourceForm())
            account, account_error = _set_account(network_resource_form)

            network_resource_type, network_resource_type_error = _set_network_resource_type(network_resource_form)

            form_valid = network_resource_form.validate_on_submit()

            # Fill up the form with useful errors for the user
            if account_error is not None:
                form_valid = False
                network_resource_form.account_id.errors.append(account_error)
            if network_resource_type_error is not None:
                form_valid = False
                network_resource_form.network_resource_type_id.errors.append(network_resource_type_error)

            # Create new network resource or return the form for new network resources with a message
            if form_valid and network_resource_type is not None:

                post_network_resource_response = InternalApi().post(
                    url_for("NetworkResourceAPI:post"),
                    args=network_resource_form.to_json(),
                    do_not_raise_for=[400, 422],
                )

                if post_network_resource_response.status_code in (200, 201):
                    network_resource_dict = post_network_resource_response.json()
                    network_resource = process_internal_api_response(
                        network_resource_dict, int(network_resource_dict["id"]), make_obj=True
                    )
                    msg = "Creation was successful."
                else:
                    current_app.logger.error(
                        f"Internal network resource API call unsuccessful [{post_network_resource_response.status_code}]: {post_network_resource_response.text}"
                    )
                    network_resource_form.process_api_validation_errors(post_network_resource_response.json())
                    if "message" in post_network_resource_response.json():
                        network_resource_form.process_api_validation_errors(
                            post_network_resource_response.json()["message"]
                        )
                        if "json" in post_network_resource_response.json()["message"]:
                            error_msg = str(
                                post_network_resource_response.json()["message"]["json"]
                            )
            if network_resource is None:
                msg = "Cannot create network resource " + error_msg
                return render_flexmeasures_template(
                    "crud/networkresource_new.html",
                    network_resource_form=network_resource_form,
                    msg=msg,
                    mapboxAccessToken=current_app.config.get("MAPBOX_ACCESS_TOKEN", ""),
                )

        else:
            network_resource_form = with_options(NetworkResourceForm())
            if not network_resource_form.validate_on_submit():
                network_resource = db.session.get(NetworkResource, id)
                # Display the form data, but set some extra data which the page wants to show.
                network_resource_info = network_resource_form.to_json()
                network_resource_info["id"] = id
                network_resource_info["account_id"] = network_resource.account_id
                network_resource = process_internal_api_response(
                    network_resource_info, int(id), make_obj=True
                )

                return render_flexmeasures_template(
                    "crud/networkresource.html",
                    network_resource_form=network_resource_form,
                    network_resource=network_resource,
                    msg="Cannot edit network resource.",
                    mapboxAccessToken=current_app.config.get("MAPBOX_ACCESS_TOKEN", ""),
                    user_can_create_network_resources=user_can_create_network_resources(),
                    user_can_delete_network_resource=user_can_delete(network_resource),
                )
            patch_network_resource_response = InternalApi().patch(
                url_for("NetworkResourceAPI:patch", id=id),
                args=network_resource_form.to_json(),
                do_not_raise_for=[400, 422],
            )
            network_resource_dict = patch_network_resource_response.json()
            if patch_network_resource_response.status_code in (200, 201):
                network_resource = process_internal_api_response(
                    network_resource_dict, int(id), make_obj=True
                )
                msg = "Editing was successful."
            else:
                current_app.logger.error(
                    f"Internal network resource API call unsuccessful [{patch_network_resource_response.status_code}]: {patch_network_resource_response.text}"
                )
                msg = "Cannot edit network resource."
                network_resource_form.process_api_validation_errors(
                    patch_network_resource_response.json().get("message")
                )
                network_resource = db.session.get(NetworkResource, id)
        # TODO: send it to network resources page with all the network resources being shown
        return render_flexmeasures_template(
            "crud/networkresources.html",
            network_resource=network_resource,
            network_resource_form=network_resource_form,
            msg=msg,
            mapboxAccessToken=current_app.config.get("MAPBOX_ACCESS_TOKEN", ""),
            user_can_create_network_resources=user_can_create_network_resources(),
            user_can_delete_network_resource=user_can_delete(network_resource),
        )

    @login_required
    def delete_with_data(self, id: str):
        """Delete via /networkresources/delete_with_data/<id>"""
        InternalApi().delete(url_for("NetworkResourceAPI:delete", id=id))
        return self.index(
            msg=f"Network Resource {id} has been deleted."
        )


def _set_account(network_resource_form: NewNetworkResourceForm) -> tuple[Account | None, str | None]:
    """Set an account for the to-be-created network resource.
    Return the account (if available) and an error message"""
    account_error = None

    if network_resource_form.account_id.data == -1:
        if user_has_admin_access(current_user, "update"):
            return None, None  # Account can be None (public network resource)
        else:
            account_error = "Please pick an existing account."

    account = db.session.execute(
        select(Account).filter_by(id=int(network_resource_form.account_id.data))
    ).scalar_one_or_none()

    if account:
        network_resource_form.account_id.data = account.id
    else:
        current_app.logger.error(account_error)
    return account, account_error


def _set_network_resource_type(
    network_resource_form: NewNetworkResourceForm,
) -> tuple[NetworkResourceType | None, str | None]:
    """Set an network resource type for the to-be-created network resource.
    Return the network resource type (if available) and an error message."""
    network_resource_type = None
    network_resource_type_error = None

    if int(network_resource_form.network_resource_type_id.data) == -1:
        network_resource_type_error = "Pick an existing network resource type."
    else:
        network_resource_type = db.session.execute(
            select(NetworkResourceType).filter_by(
                id=int(network_resource_form.network_resource_type_id.data)
            )
        ).scalar_one_or_none()

    if network_resource_type:
        network_resource_form.network_resource_type_id.data = network_resource_type.id
    else:
        current_app.logger.error(network_resource_type_error)
    return network_resource_type, network_resource_type_error
