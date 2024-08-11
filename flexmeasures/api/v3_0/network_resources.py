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
from flexmeasures.data.models.network_resources import NetworkResource
from flexmeasures.data.schemas import AwareDateTimeField
from flexmeasures.data.schemas.generic_assets import GenericAssetSchema as AssetSchema
from flexmeasures.data.schemas.network_resource import NetworkResourceSchema
from flexmeasures.api.common.schemas.generic_assets import AssetIdField
from flexmeasures.api.common.schemas.network_resources import NetworkResourceIdField
from flexmeasures.api.common.schemas.users import AccountIdField
from flexmeasures.utils.coding_utils import flatten_unique
from flexmeasures.ui.utils.view_utils import set_session_variables

"""
API endpoints to manage accounts.

Both POST (to create) and DELETE are not accessible via the API, but as CLI functions.
Editing (PATCH) is also not yet implemented, but might be next, e.g. for the name or roles.
"""

# Instantiate schemas outside of endpoint logic to minimize response time
network_resource_schema = NetworkResourceSchema()
network_resources_schema = NetworkResourceSchema(many=True)
partial_network_resource_schema = NetworkResourceSchema(partial=True, exclude=["account_id"])


class NetworkResourceAPI(FlaskView):
    """
    This API view exposes network resources.
    """

    route_base = "/networkresources"
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
        """List all netowork resources owned by a certain account.

        .. :quickref: Network Resource; Download network resource list

        This endpoint returns all accessible network resources for the account of the user.
        The `account_id` query parameter can be used to list network resources from a different account.

        **Example response**

        An example of one network resource being returned:

        .. sourcecode:: json

            [
                {
                    "id": 1,
                    "name": "Test battery",
                    "latitude": 10,
                    "longitude": 100,
                    "account_id": 2,
                    "generic_network_resource_type_id": 1
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
        return network_resources_schema.dump(account.network_resources), 200

    @route("/public", methods=["GET"])
    @as_json
    def public(self):
        """Return all public network resources.

        .. :quickref: Asset; Return all network resources.

        This endpoint returns all network resources.

        :reqheader Authorization: The authentication token
        :reqheader Content-Type: application/json
        :resheader Content-Type: application/json
        :status 200: PROCESSED
        :status 400: INVALID_REQUEST
        :status 401: UNAUTHORIZED
        :status 422: UNPROCESSABLE_ENTITY
        """
        network_resources = db.session.scalars(
            select(NetworkResource).filter(NetworkResource.account_id.is_(None))
        ).all()
        return network_resources_schema.dump(network_resources), 200

    @route("", methods=["POST"])
    @permission_required_for_context(
        "create-children", ctx_loader=AccountIdField.load_current
    )
    @use_args(network_resource_schema)
    def post(self, network_resource_data: dict):
        """Create new network resource.

        .. :quickref: NetworkResource; Create a new network resource

        This endpoint creates a new network resource.

        **Example request**

        .. sourcecode:: json

            {
                "name": "Test battery",
                "network_resourcw_type_id": 2,
                "account_id": 2,
                "latitude": 40,
                "longitude": 170.3,
            }


        The newly posted network resource is returned in the response.

        :reqheader Authorization: The authentication token
        :reqheader Content-Type: application/json
        :resheader Content-Type: application/json
        :status 201: CREATED
        :status 400: INVALID_REQUEST
        :status 401: UNAUTHORIZED
        :status 403: INVALID_SENDER
        :status 422: UNPROCESSABLE_ENTITY
        """
        network_resource = NetworkResource(**network_resource_data)
        db.session.add(network_resource)
        db.session.commit()
        return network_resource_schema.dump(network_resource), 201

    @route("/<id>", methods=["GET"])
    @use_kwargs({"network_resource": NetworkResourceIdField(data_key="id")}, location="path")
    @permission_required_for_context("read", ctx_arg_name="network_resource")
    @as_json
    def fetch_one(self, id, network_resource):
        """Fetch a given network resource.

        .. :quickref: Network Resource; Get an network resource

        This endpoint gets a network resource.

        **Example response**

        .. sourcecode:: json

            {
                "network_resource_type_id": 2,
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
        return network_resource_schema.dump(network_resource), 200

    @route("/<id>", methods=["PATCH"])
    @use_args(partial_network_resource_schema)
    @use_kwargs({"db_network_resource": NetworkResourceIdField(data_key="id")}, location="path")
    @permission_required_for_context("update", ctx_arg_name="db_network_resource")
    @as_json
    def patch(self, network_resource_data: dict, id: int, db_network_resource: NetworkResource):
        """Update an network resource given its identifier.

        .. :quickref: Network Resource; Update an network resource

        This endpoint sets data for an existing network resource.
        Any subset of network resource fields can be sent.

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

        The whole network resource is returned in the response:

        .. sourcecode:: json

            {
                "generic_network_resource_type_id": 2,
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
        for k, v in network_resource_data.items():
            setattr(db_network_resource, k, v)
        db.session.add(db_network_resource)
        db.session.commit()
        return network_resource_schema.dump(db_network_resource), 200

    @route("/<id>", methods=["DELETE"])
    @use_kwargs({"network_resource": NetworkResourceIdField(data_key="id")}, location="path")
    @permission_required_for_context("delete", ctx_arg_name="network_resource")
    @as_json
    def delete(self, id: int, network_resource: NetworkResource):
        """Delete a network resource given its identifier.

        .. :quickref: NetworkResource; Delete a network resource

        This endpoint deletes an existing network resource, as well as all sensors and measurements recorded for it.

        :reqheader Authorization: The authentication token
        :reqheader Content-Type: application/json
        :resheader Content-Type: application/json
        :status 204: DELETED
        :status 400: INVALID_REQUEST, REQUIRED_INFO_MISSING, UNEXPECTED_PARAMS
        :status 401: UNAUTHORIZED
        :status 403: INVALID_SENDER
        :status 422: UNPROCESSABLE_ENTITY
        """
        network_resource_name = network_resource.name
        db.session.execute(delete(NetworkResource).filter_by(id=network_resource.id))
        db.session.commit()
        current_app.logger.info("Deleted network resource '%s'." % network_resource_name)
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
    #     """GET from /assets/<id>/chart

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
    #     """GET from /assets/<id>/chart_data

    #     .. :quickref: Chart; Download time series for use in charts

    #     Data for use in charts (in case you have the chart specs already).
    #     """
    #     sensors = flatten_unique(asset.sensors_to_show)
    #     return asset.search_beliefs(sensors=sensors, as_json=True, **kwargs)
