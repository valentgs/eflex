from flask import abort
from flexmeasures.data.schemas import network_resource
from marshmallow import fields
from sqlalchemy import select

from flexmeasures.data import db
from flexmeasures.data.models.network_resources import NetworkResource


class NetworkResourceIdField(fields.Integer):
    """
    Field that represents a Network Resource ID. It de-serializes from the network resource id to a network resource instance.
    """

    def _deserialize(self, network_resource_id: int, attr, obj, **kwargs) -> NetworkResource:
        network_resource: NetworkResource = db.session.execute(
            select(NetworkResource).filter_by(id=int(network_resource_id))
        ).scalar_one_or_none()
        if network_resource is None:
            raise abort(404, f"Network Resource {network_resource_id} not found")
        return network_resource

    def _serialize(self, network_resource: NetworkResource, attr, data, **kwargs) -> int:
        return network_resource.id
