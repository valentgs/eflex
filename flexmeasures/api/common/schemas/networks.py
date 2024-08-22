from flask import abort
from marshmallow import fields
from sqlalchemy import select

from flexmeasures.data import db
from flexmeasures.data.models.networks import Network


class NetworkIdField(fields.Integer):
    """
    Field that represents a network ID. It de-serializes from the network id to an network instance.
    """

    def _deserialize(self, network_id: int, attr, obj, **kwargs) -> Network:
        network: Network = db.session.execute(
            select(Network).filter_by(id=int(network_id))
        ).scalar_one_or_none()
        if network is None:
            raise abort(404, f"Network {network_id} not found")
        return network

    def _serialize(self, network: Network, attr, data, **kwargs) -> int:
        return network.id
