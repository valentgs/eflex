# from __future__ import annotations

import json

from marshmallow import validates, ValidationError, fields, validates_schema
from flask_security import current_user
from sqlalchemy import select

from flexmeasures.data import ma, db
from flexmeasures.data.models.user import Account
from flexmeasures.data.models.networks import Network
from flexmeasures.data.schemas.utils import (
    FMValidationError,
    MarshmallowClickMixin,
    with_appcontext_if_needed,
)
from flexmeasures.auth.policy import user_has_admin_access
from flexmeasures.cli import is_running as running_as_cli
from flexmeasures.utils.coding_utils import flatten_unique


class JSON(fields.Field):
    def _deserialize(self, value, attr, data, **kwargs) -> dict:
        try:
            return json.loads(value)
        except ValueError:
            raise ValidationError("Not a valid JSON string.")

    def _serialize(self, value, attr, data, **kwargs) -> str:
        return json.dumps(value)


class NetworkSchema(ma.SQLAlchemySchema):
    """
    NetworkResource schema, with validations.
    """

    id = ma.auto_field(dump_only=True)
    name = fields.Str(required=True)
    network_resources = fields.List(fields.Integer(), required=True)
    account_id = ma.auto_field()

    class Meta:
        model = Network

    # @validates_schema(skip_on_field_errors=False)
    # def validate_name_is_unique_under_parent(self, data, **kwargs):
    #     if "name" in data:

    #         network_resource = db.session.scalars(
    #             select(NetworkResource)
    #             .filter_by(
    #                 name=data["name"],
    #                 # parent_asset_id=data.get("parent_asset_id"),
    #                 account_id=data.get("account_id"),
    #             )
    #             .limit(1)
    #         ).first()

    #         if network_resource:
    #             raise ValidationError(
    #                 f"An network resource with the name '{data['name']}' already exists.",
    #                 "name",
    #             )

    # @validates("network_resource_type_id")
    # def validate_network_resource_type(self, network_resource_type_id: int):
    #     network_resource_type = db.session.get(NetworkResourceType, network_resource_type_id)
    #     if not network_resource_type:
    #         raise ValidationError(
    #             f"NetworkResourceType with id {network_resource_type_id} doesn't exist."
    #         )

    # @validates("parent_asset_id")
    # def validate_parent_asset(self, parent_asset_id: int | None):
    #     if parent_asset_id is not None:
    #         parent_asset = db.session.get(GenericAsset, parent_asset_id)
    #         if not parent_asset:
    #             raise ValidationError(
    #                 f"Parent GenericAsset with id {parent_asset_id} doesn't exist."
    #             )

    # @validates("account_id")
    # def validate_account(self, account_id: int | None):
    #     if account_id is None and (
    #         running_as_cli() or user_has_admin_access(current_user, "update")
    #     ):
    #         return
    #     account = db.session.get(Account, account_id)
    #     if not account:
    #         raise ValidationError(f"Account with Id {account_id} doesn't exist.")
    #     if not running_as_cli() and (
    #         not user_has_admin_access(current_user, "update")
    #         and account_id != current_user.account_id
    #     ):
    #         raise ValidationError(
    #             "User is not allowed to create assets for this account."
    #         )

    # @validates("attributes")
    # def validate_attributes(self, attributes: dict):
    #     sensors_to_show = attributes.get("sensors_to_show", [])

    #     # Check type
    #     if not isinstance(sensors_to_show, list):
    #         raise ValidationError("sensors_to_show should be a list.")
    #     for sensor_listing in sensors_to_show:
    #         if not isinstance(sensor_listing, (int, list)):
    #             raise ValidationError(
    #                 "sensors_to_show should only contain sensor IDs (integers) or lists thereof."
    #             )
    #         if isinstance(sensor_listing, list):
    #             for sensor_id in sensor_listing:
    #                 if not isinstance(sensor_id, int):
    #                     raise ValidationError(
    #                         "sensors_to_show should only contain sensor IDs (integers) or lists thereof."
    #                     )

    #     # Check whether IDs represent accessible sensors
    #     from flexmeasures.data.schemas import SensorIdField

    #     sensor_ids = flatten_unique(sensors_to_show)
    #     for sensor_id in sensor_ids:
    #         SensorIdField().deserialize(sensor_id)



class NetworkIdField(MarshmallowClickMixin, fields.Int):
    """Field that deserializes to a Network and serializes back to an integer."""

    @with_appcontext_if_needed()
    def _deserialize(self, value, attr, obj, **kwargs) -> Network:
        """Turn a network id into a Network."""
        network = db.session.get(Network, value)
        if network is None:
            raise FMValidationError(f"No network resource found with id {value}.")
        # lazy loading now (asset is somehow not in session after this)
        # network_resource.network_resource_type
        return network

    def _serialize(self, network, attr, data, **kwargs):
        """Turn a GenericAsset into a generic asset id."""
        return network.id
