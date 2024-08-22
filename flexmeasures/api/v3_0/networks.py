import json

from flask import current_app
from flask_classful import FlaskView, route
from flask_security import auth_required
from flask_json import as_json
from marshmallow import fields
from webargs.flaskparser import use_kwargs, use_args
from sqlalchemy import select, delete

from flexmeasures.auth.decorators import permission_required_for_context
from flexmeasures.data import db
from flexmeasures.data.models.user import Account
from flexmeasures.data.models.generic_assets import GenericAsset
from flexmeasures.data.models.networks import Network
from flexmeasures.data.schemas import AwareDateTimeField
from flexmeasures.data.schemas.generic_assets import GenericAssetSchema as AssetSchema
from flexmeasures.data.schemas.networks import NetworkSchema
from flexmeasures.api.common.schemas.generic_assets import AssetIdField
from flexmeasures.api.common.schemas.networks import NetworkIdField
from flexmeasures.api.common.schemas.users import AccountIdField
from flexmeasures.utils.coding_utils import flatten_unique
from flexmeasures.ui.utils.view_utils import set_session_variables


network_schema = NetworkSchema()
networks_schema = NetworkSchema(many=True)
partial_network_schema = NetworkSchema(partial=True, exclude=["account_id"])


class NetworkAPI(FlaskView):
    """
    This API view exposes generic assets.
    Under development until it replaces the original Asset API.
    """

    route_base = "/networks"
    trailing_slash = False
    decorators = [auth_required()]

    @route("", methods=["GET"])
    @use_kwargs(
        {
            "account": AccountIdField(
                data_key="account_id", load_default=AccountIdField.load_current
            ),
        },
        location="query",
    )
    @permission_required_for_context("read", ctx_arg_name="account")
    @as_json
    def index(self, account: Account):
        """List all networks owned by a certain account.

        .. :quickref: Asset; Download asset list

        This endpoint returns all accessible assets for the account of the user.
        The `account_id` query parameter can be used to list assets from a different account.

        **Example response**

        An example of one asset being returned:

        .. sourcecode:: json

            [
                {
                    "id": 1,
                    "name": "Test battery",
                    "latitude": 10,
                    "longitude": 100,
                    "account_id": 2,
                    "generic_asset_type_id": 1
                }
            ]

        :reqheader Authorization: The authentication token
        :reqheader Content-Type: application/json
        :resheader Content-Type: application/json
        :status 200: PROCESSED
        :status 400: INVALID_REQUEST
        :status 401: UNAUTHORIZED
        :status 403: INVALID_SENDER
        :status 422: UNPROCESSABLE_ENTITY
        """
        return networks_schema.dump(account.networks), 200

    @route("/public", methods=["GET"])
    @as_json
    def public(self):
        """Return all public networks.

        .. :quickref: Network; Return all public networks.

        This endpoint returns all public networks.

        :reqheader Authorization: The authentication token
        :reqheader Content-Type: application/json
        :resheader Content-Type: application/json
        :status 200: PROCESSED
        :status 400: INVALID_REQUEST
        :status 401: UNAUTHORIZED
        :status 422: UNPROCESSABLE_ENTITY
        """
        networks = db.session.scalars(
            select(Network).filter(Network.account_id.is_(None))
        ).all()
        return networks_schema.dump(networks), 200

    @route("", methods=["POST"])
    @permission_required_for_context(
        "create-children", ctx_loader=AccountIdField.load_current
    )
    @use_args(network_schema)
    def post(self, network_data: dict):
        """Create new network.

        .. :quickref: Network; Create a new network

        This endpoint creates a new network.

        **Example request**

        .. sourcecode:: json

            {
                "name": "Test battery",
                "generic_asset_type_id": 2,
                "account_id": 2,
                "latitude": 40,
                "longitude": 170.3,
            }


        The newly posted asset is returned in the response.

        :reqheader Authorization: The authentication token
        :reqheader Content-Type: application/json
        :resheader Content-Type: application/json
        :status 201: CREATED
        :status 400: INVALID_REQUEST
        :status 401: UNAUTHORIZED
        :status 403: INVALID_SENDER
        :status 422: UNPROCESSABLE_ENTITY
        """        
        network = Network(**network_data)
        db.session.add(network)
        db.session.commit()
        return network_schema.dump(network), 201

    @route("/<id>", methods=["GET"])
    @use_kwargs({"network": NetworkIdField(data_key="id")}, location="path")
    @permission_required_for_context("read", ctx_arg_name="network")
    @as_json
    def fetch_one(self, id, network):
        """Fetch a given network.

        .. :quickref: Network; Get an network

        This endpoint gets a network.

        **Example response**

        .. sourcecode:: json

            {
                "generic_asset_type_id": 2,
                "name": "Test battery",
                "id": 1,
                "latitude": 10,
                "longitude": 100,
                "account_id": 1,
            }

        :reqheader Authorization: The authentication token
        :reqheader Content-Type: application/json
        :resheader Content-Type: application/json
        :status 200: PROCESSED
        :status 400: INVALID_REQUEST, REQUIRED_INFO_MISSING, UNEXPECTED_PARAMS
        :status 401: UNAUTHORIZED
        :status 403: INVALID_SENDER
        :status 422: UNPROCESSABLE_ENTITY
        """
        return network_schema.dump(network), 200

    @route("/<id>", methods=["PATCH"])
    @use_args(partial_network_schema)
    @use_kwargs({"db_network": NetworkIdField(data_key="id")}, location="path")
    @permission_required_for_context("update", ctx_arg_name="db_network")
    @as_json
    def patch(self, network_data: dict, id: int, db_network: Network):
        """Update an network given its identifier.

        .. :quickref: Network; Update an network

        This endpoint sets data for an existing network.
        Any subset of network fields can be sent.

        The following fields are not allowed to be updated:
        - id
        - account_id

        **Example request**

        .. sourcecode:: json

            {
                "latitude": 11.1,
                "longitude": 99.9,
            }


        **Example response**

        The whole network is returned in the response:

        .. sourcecode:: json

            {
                "generic_asset_type_id": 2,
                "id": 1,
                "latitude": 11.1,
                "longitude": 99.9,
                "name": "Test battery",
                "account_id": 2,
            }

        :reqheader Authorization: The authentication token
        :reqheader Content-Type: application/json
        :resheader Content-Type: application/json
        :status 200: UPDATED
        :status 400: INVALID_REQUEST, REQUIRED_INFO_MISSING, UNEXPECTED_PARAMS
        :status 401: UNAUTHORIZED
        :status 403: INVALID_SENDER
        :status 422: UNPROCESSABLE_ENTITY
        """
        for k, v in network_data.items():
            setattr(db_network, k, v)
        db.session.add(db_network)
        db.session.commit()
        return network_schema.dump(db_network), 200

    @route("/<id>", methods=["DELETE"])
    @use_kwargs({"network": NetworkIdField(data_key="id")}, location="path")
    @permission_required_for_context("delete", ctx_arg_name="network")
    @as_json
    def delete(self, id: int, network: Network):
        """Delete an network given its identifier.

        .. :quickref: Network; Delete a network

        This endpoint deletes an existing network, as well as all sensors and measurements recorded for it.

        :reqheader Authorization: The authentication token
        :reqheader Content-Type: application/json
        :resheader Content-Type: application/json
        :status 204: DELETED
        :status 400: INVALID_REQUEST, REQUIRED_INFO_MISSING, UNEXPECTED_PARAMS
        :status 401: UNAUTHORIZED
        :status 403: INVALID_SENDER
        :status 422: UNPROCESSABLE_ENTITY
        """
        network_name = network.name
        db.session.execute(delete(Network).filter_by(id=network.id))
        db.session.commit()
        current_app.logger.info("Deleted network '%s'." % network_name)
        return {}, 204

    # @route("/<id>/chart", strict_slashes=False)  # strict on next version? see #1014
    # @use_kwargs(
    #     {"asset": AssetIdField(data_key="id")},
    #     location="path",
    # )
    # @use_kwargs(
    #     {
    #         "event_starts_after": AwareDateTimeField(format="iso", required=False),
    #         "event_ends_before": AwareDateTimeField(format="iso", required=False),
    #         "beliefs_after": AwareDateTimeField(format="iso", required=False),
    #         "beliefs_before": AwareDateTimeField(format="iso", required=False),
    #         "include_data": fields.Boolean(required=False),
    #         "dataset_name": fields.Str(required=False),
    #         "height": fields.Str(required=False),
    #         "width": fields.Str(required=False),
    #     },
    #     location="query",
    # )
    # @permission_required_for_context("read", ctx_arg_name="asset")
    # def get_chart(self, id: int, asset: GenericAsset, **kwargs):
    #     """GET from /networks/<id>/chart

    #     .. :quickref: Chart; Download a chart with time series
    #     """
    #     # Store selected time range as session variables, for a consistent UX across UI page loads
    #     set_session_variables("event_starts_after", "event_ends_before")
    #     return json.dumps(asset.chart(**kwargs))

    # @route(
    #     "/<id>/chart_data", strict_slashes=False
    # )  # strict on next version? see #1014
    # @use_kwargs(
    #     {"asset": AssetIdField(data_key="id")},
    #     location="path",
    # )
    # @use_kwargs(
    #     {
    #         "event_starts_after": AwareDateTimeField(format="iso", required=False),
    #         "event_ends_before": AwareDateTimeField(format="iso", required=False),
    #         "beliefs_after": AwareDateTimeField(format="iso", required=False),
    #         "beliefs_before": AwareDateTimeField(format="iso", required=False),
    #         "most_recent_beliefs_only": fields.Boolean(required=False),
    #     },
    #     location="query",
    # )
    # @permission_required_for_context("read", ctx_arg_name="asset")
    # def get_chart_data(self, id: int, asset: GenericAsset, **kwargs):
    #     """GET from /networks/<id>/chart_data

    #     .. :quickref: Chart; Download time series for use in charts

    #     Data for use in charts (in case you have the chart specs already).
    #     """
    #     sensors = flatten_unique(asset.sensors_to_show)
    #     return asset.search_beliefs(sensors=sensors, as_json=True, **kwargs)
