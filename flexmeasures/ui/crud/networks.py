from __future__ import annotations

import copy
import json

from flask import url_for, current_app, request
from flask_classful import FlaskView, route
from flask_wtf import FlaskForm
from flask_security import login_required, current_user
from webargs.flaskparser import use_kwargs
from wtforms import StringField, DecimalField, SelectField, SelectMultipleField, widgets
from wtforms.validators import DataRequired, optional
from sqlalchemy import select
from flexmeasures.auth.policy import user_has_admin_access

from flexmeasures.data import db
from flexmeasures.auth.error_handling import unauthorized_handler
from flexmeasures.auth.policy import check_access
from flexmeasures.data.schemas import StartEndTimeSchema, network_resource
from flexmeasures.data.services.job_cache import NoRedisConfigured
from flexmeasures.data.models.generic_assets import (
    GenericAssetType,
    GenericAsset,
    get_center_location_of_assets,
)
from flexmeasures.data.models.networks import Network
from flexmeasures.data.models.network_resources import NetworkResource
from flexmeasures.data.models.user import Account
from flexmeasures.data.models.time_series import Sensor
from flexmeasures.ui.utils.view_utils import render_flexmeasures_template
from flexmeasures.ui.crud.api_wrapper import InternalApi
from flexmeasures.data.services.sensors import (
    build_sensor_status_data,
    build_asset_jobs_data,
)


"""
Network crud view.

Note: This uses the internal dev API version
      ― if those endpoints get moved or updated to a higher version,
      we probably should change the version used here, as well.
"""


class NetworkForm(FlaskForm):
    """The default asset form only allows to edit the name and location."""

    name = StringField("Name")
    network_resources = SelectMultipleField(
        "Network Resources", 
        option_widget=widgets.CheckboxInput(),
        widget=widgets.ListWidget(prefix_label=False)
    )

    def __init__(self, *args, **kwargs):
        super(NetworkForm, self).__init__(*args, **kwargs)
        
        # Ensure that the database query runs within an application context
        with current_app.app_context():
            self.network_resources.choices = [
                (str(resource.id), resource.name) for resource in NetworkResource.query.all()
            ]

    def validate_on_submit(self):
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


class NewNetworkForm(NetworkForm):
    """Here, in addition, we allow to set account."""
    account_id = SelectField("Account", coerce=int)


def with_options(form: NetworkForm | NewNetworkForm) -> NetworkForm | NewNetworkForm:
    if "account_id" in form:
        form.account_id.choices = [(-1, "--Select account--")] + [
            (account.id, account.name)
            for account in db.session.scalars(select(Account)).all()
        ]
    return form


def process_internal_api_response(
    network_data: dict, network_id: int | None = None, make_obj=False
) -> Network | dict:
    """
    Turn data from the internal API into something we can use to further populate the UI.
    Either as an network object or a dict for form filling.

    If we add other data by querying the database, we make sure the network is not in the session afterwards.
    """
    if network_id is None:
        network_id = network_data["id"]

    def expunge_network():
        # use if no insert is wanted from a previous query which flushes its results
        if network in db.session:
            db.session.expunge(network)

    network_data.pop("status", None)  # might have come from requests.response
    if network_id:
        network_data["id"] = network_id
    if make_obj:
        children = network_data.pop("child_networks", [])
        network = Network(
            **{
                **network_data
            }
        )  # TODO: use schema?

        expunge_network()
        network.owner = db.session.get(Account, network_data["account_id"])
        expunge_network()
        db.session.flush()
        expunge_network()

        return network
    return network_data


def user_can_create_networks() -> bool:
    try:
        check_access(current_user.account, "create-children")
    except Exception:
        return False
    return True


def user_can_delete(network) -> bool:
    try:
        check_access(network, "delete")
    except Exception:
        return False
    return True


def get_networks_by_account(account_id: int | str | None) -> list[Network]:

    if account_id is not None:
        get_networks_response = InternalApi().get(
            url_for("NetworkAPI:index"), query={"account_id": account_id}
        )
    else:
        get_networks_response = InternalApi().get(url_for("NetworkAPI:public"))
    return [
        process_internal_api_response(ad, make_obj=True)
        for ad in get_networks_response.json()
    ]


class NetworkCrudUI(FlaskView):
    """
    These views help us offer a Jinja2-based UI.
    The main focus on logic is the API, so these views simply call the API functions,
    and deal with the response.
    Some new functionality, like fetching accounts, is added here.
    """

    route_base = "/networks"
    trailing_slash = False

    @login_required
    def index(self, msg=""):
        """GET from /networks

        List the user's assets. For admins, list across all accounts.
        """
        networks = []

        if user_has_admin_access(current_user, "read"):
            for account in db.session.scalars(select(Account)).all():
                networks += get_networks_by_account(account.id)
            networks += get_networks_by_account(account_id=None)
        else:
            networks = get_networks_by_account(current_user.account_id)

        return render_flexmeasures_template(
            "crud/networks.html",
            networks=networks,
            message=msg,
            user_can_create_networks=user_can_create_networks(),
        )

    # @login_required
    # def owned_by(self, account_id: str):
    #     """/networks/owned_by/<account_id>"""
    #     msg = ""
    #     get_assets_response = InternalApi().get(
    #         url_for("AssetAPI:index"),
    #         query={"account_id": account_id},
    #         do_not_raise_for=[404],
    #     )
    #     if get_assets_response.status_code == 404:
    #         assets = []
    #         msg = f"Account {account_id} unknown."
    #     else:
    #         assets = [
    #             process_internal_api_response(ad, make_obj=True)
    #             for ad in get_assets_response.json()
    #         ]
    #     db.session.flush()
    #     return render_flexmeasures_template(
    #         "crud/networks.html",
    #         account=db.session.get(Account, account_id),
    #         assets=assets,
    #         msg=msg,
    #         user_can_create_networks=user_can_create_networks(),
    #     )

    @use_kwargs(StartEndTimeSchema, location="query")
    @login_required
    def get(self, id: str, **kwargs):
        """GET from /networks/<id> where id can be 'new' (and thus the form for network creation is shown)
        The following query parameters are supported (should be used only together):
         - start_time: minimum time of the events to be shown
         - end_time: maximum time of the events to be shown
        """
        if id == "new":
            if not user_can_create_networks():
                return unauthorized_handler(None, [])

            network_form = with_options(NewNetworkForm())
            return render_flexmeasures_template(
                "crud/network_new.html",
                network_form=network_form,
                msg="",
                map_center=get_center_location_of_assets(user=current_user),
                mapboxAccessToken=current_app.config.get("MAPBOX_ACCESS_TOKEN", ""),
            )

        get_network_response = InternalApi().get(url_for("NetworkAPI:fetch_one", id=id))
        network_dict = get_network_response.json()

        network_form = with_options(NetworkForm())

        network = process_internal_api_response(network_dict, int(id), make_obj=True)
        network_form.process(data=process_internal_api_response(network_dict))

        return render_flexmeasures_template(
            "crud/network.html",
            network=network,
            network_form=network_form,
            msg="",
            user_can_create_networks=user_can_create_networks(),
            user_can_delete_network=user_can_delete(network)
        )

    # @login_required
    # @route("/<id>/status")
    # def status(self, id: str):
    #     """GET from /networks/<id>/status to show the staleness of the asset's sensors."""

    #     get_asset_response = InternalApi().get(url_for("AssetAPI:fetch_one", id=id))
    #     asset_dict = get_asset_response.json()

    #     asset = process_internal_api_response(asset_dict, int(id), make_obj=True)
    #     status_data = build_sensor_status_data(asset)

    #     # add data about forecasting and scheduling jobs
    #     redis_connection_err = None
    #     scheduling_job_data, forecasting_job_data = list(), list()
    #     try:
    #         jobs_data = build_asset_jobs_data(asset)
    #     except NoRedisConfigured as e:
    #         redis_connection_err = e.args[0]
    #     else:
    #         scheduling_job_data = [
    #             jd for jd in jobs_data if jd["queue"] == "scheduling"
    #         ]
    #         forecasting_job_data = [
    #             jd for jd in jobs_data if jd["queue"] == "forecasting"
    #         ]

    #     return render_flexmeasures_template(
    #         "views/status.html",
    #         asset=asset,
    #         sensors=status_data,
    #         scheduling_job_data=scheduling_job_data,
    #         forecasting_job_data=forecasting_job_data,
    #         redis_connection_err=redis_connection_err,
    #     )

    @login_required
    def post(self, id: str):
        """POST to /networks/<id>, where id can be 'create' (and thus a new asset is made from POST data)
        Most of the code deals with creating a user for the asset if no existing is chosen.
        """

        network: Network = None
        error_msg = ""

        if id == "create":
            network_form = with_options(NewNetworkForm())

            account, account_error = _set_account(network_form)

            form_valid = network_form.validate_on_submit()

            # Fill up the form with useful errors for the user
            if account_error is not None:
                form_valid = False
                network_form.account_id.errors.append(account_error)
            
            # Create new network or return the form for new networks with a message
            if form_valid:
                post_network_response = InternalApi().post(
                    url_for("NetworkAPI:post"),
                    args=network_form.to_json(),
                    do_not_raise_for=[400, 422],
                )
                if post_network_response.status_code in (200, 201):
                    network_dict = post_network_response.json()
                    network = process_internal_api_response(
                        network_dict, int(network_dict["id"]), make_obj=True
                    )
                    msg = "Creation was successful."
                else:
                    current_app.logger.error(
                        f"Internal network API call unsuccessful [{post_network_response.status_code}]: {post_network_response.text}"
                    )
                    network_form.process_api_validation_errors(post_network_response.json())
                    if "message" in post_network_response.json():
                        network_form.process_api_validation_errors(
                            post_network_response.json()["message"]
                        )
                        if "json" in post_network_response.json()["message"]:
                            error_msg = str(
                                post_network_response.json()["message"]["json"]
                            )
            if network is None:
                msg = "Cannot create network. " + error_msg
                return render_flexmeasures_template(
                    "crud/network_new.html",
                    network_form=network_form,
                    msg=msg
                )

        else:
            network_form = with_options(NetworkForm())
            if not network_form.validate_on_submit():
                network = db.session.get(Network, id)
                # Display the form data, but set some extra data which the page wants to show.
                network_info = network_form.to_json()
                network_info["id"] = id
                network_info["account_id"] = network.account_id
                network = process_internal_api_response(
                    network_info, int(id), make_obj=True
                )

                return render_flexmeasures_template(
                    "crud/network.html",
                    network_form=network_form,
                    network=network,
                    msg="Cannot edit network.",
                    user_can_create_networks=user_can_create_networks(),
                    user_can_delete_asset=user_can_delete(network),
                )
            patch_network_response = InternalApi().patch(
                url_for("NetworkAPI:patch", id=id),
                args=network_form.to_json(),
                do_not_raise_for=[400, 422],
            )
            network_dict = patch_network_response.json()
            if patch_network_response.status_code in (200, 201):
                network = process_internal_api_response(
                    network_dict, int(id), make_obj=True
                )
                msg = "Editing was successful."
            else:
                current_app.logger.error(
                    f"Internal network API call unsuccessful [{patch_network_response.status_code}]: {patch_network_response.text}"
                )
                msg = "Cannot edit network."
                network_form.process_api_validation_errors(
                    patch_network_response.json().get("message")
                )
                network = db.session.get(Network, id)

        return render_flexmeasures_template(
            "crud/network.html",
            network=network,
            network_form=network_form,
            msg=msg,
            user_can_create_networks=user_can_create_networks(),
            user_can_delete_network=user_can_delete(network),
        )

    @login_required
    def delete_with_data(self, id: str):
        """Delete via /networks/delete_with_data/<id>"""
        InternalApi().delete(url_for("NetworkAPI:delete", id=id))
        return self.index(
            msg=f"Network {id} has been deleted."
        )


def _set_account(network_form: NewNetworkForm) -> tuple[Account | None, str | None]:
    """Set an account for the to-be-created network.
    Return the account (if available) and an error message"""
    account_error = None

    if network_form.account_id.data == -1:
        if user_has_admin_access(current_user, "update"):
            return None, None  # Account can be None (public network)
        else:
            account_error = "Please pick an existing account."

    account = db.session.execute(
        select(Account).filter_by(id=int(network_form.account_id.data))
    ).scalar_one_or_none()

    if account:
        network_form.account_id.data = account.id
    else:
        current_app.logger.error(account_error)
    return account, account_error


# def _set_asset_type(
#     network_form: NewAssetForm,
# ) -> tuple[GenericAssetType | None, str | None]:
#     """Set an asset type for the to-be-created asset.
#     Return the asset type (if available) and an error message."""
#     asset_type = None
#     asset_type_error = None

#     if int(network_form.generic_asset_type_id.data) == -1:
#         asset_type_error = "Pick an existing asset type."
#     else:
#         asset_type = db.session.execute(
#             select(GenericAssetType).filter_by(
#                 id=int(network_form.generic_asset_type_id.data)
#             )
#         ).scalar_one_or_none()

#     if asset_type:
#         network_form.generic_asset_type_id.data = asset_type.id
#     else:
#         current_app.logger.error(asset_type_error)
#     return asset_type, asset_type_error
