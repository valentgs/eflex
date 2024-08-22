from __future__ import annotations

import copy
import json

from flask import url_for, current_app, request
from flask_classful import FlaskView, route
from flask_wtf import FlaskForm
from flask_security import login_required, current_user
from flexmeasures.data.models.network_resources import NetworkResource
from flexmeasures.data.models.networks import Network
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
from flexmeasures.data.services.opf import eflex_opf, eflex_pf
from datetime import datetime
from flexmeasures.utils.time_utils import server_now
from flexmeasures.ui.crud.networks import get_networks_by_account

"""
WRITE SOMETHING
"""

class RunOPFUI(FlaskView):
    """
    These views help us offer a Jinja2-based UI.
    The main focus on logic is the API, so these views simply call the API functions,
    and deal with the response.
    Some new functionality, like fetching accounts and asset types, is added here.
    """

    route_base = "/opf"
    trailing_slash = False

    @login_required
    @route("/run")
    def run(self):
        """POST to /assets/<id>, where id can be 'create' (and thus a new asset is made from POST data)
        Most of the code deals with creating a user for the asset if no existing is chosen.
        """
        network_name = request.args.get('network_name')
        from_day = request.args.get('from_day')
        to_day = request.args.get('to_day')
        from_time = request.args.get('from_time')
        to_time = request.args.get('to_time')
        OPForPF = request.args.get('PForOPF')

        from_datetime = datetime.strptime(from_day + " " + from_time, "%Y-%m-%d %H:%M")
        to_datetime = datetime.strptime(to_day + " " + to_time, "%Y-%m-%d %H:%M")

        networks = get_networks_by_account(current_user.account_id)
        the_network = Network.query.filter_by(name=network_name).first()

        shunt = []
        trafo = []
        buses = []
        lines = []
        for network_resource in the_network.network_resources:
            if NetworkResource.query.get(network_resource).network_resource_type_id == 1:
                buses.append(network_resource)
            if NetworkResource.query.get(network_resource).network_resource_type_id == 0:
                lines.append(network_resource)
            if NetworkResource.query.get(network_resource).network_resource_type_id == 2:
                trafo.append(network_resource)
            if NetworkResource.query.get(network_resource).network_resource_type_id == 4:
                shunt.append(network_resource)

        batteries = []
        loads = []
        external_grids = []
        for asset in GenericAsset.query.all():
            if asset.attributes.get("bus") in buses:
                if asset.generic_asset_type_id == 5:
                    if(GenericAsset.query.get(asset.id).get_attribute("slack") == "True"):
                        external_grids.append(asset.id)
                    else:
                        batteries.append(asset.id)
                if asset.generic_asset_type_id == 6:
                    loads.append(asset.id)

        duration = to_datetime - from_datetime

        import pytz
        timezone = pytz.FixedOffset(180)  # 180 minutes = 3 hours
        
        from_datetime = from_datetime.astimezone(timezone)
        to_datetime = to_datetime.astimezone(timezone)
        
        now_ = datetime.now()  # Example server time
        now = now_.astimezone(timezone)

        scheduling_kwargs = dict(
            start        = from_datetime,
            end          = to_datetime,
            load         = sorted(loads),
            battery      = sorted(batteries),
            lines        = sorted(lines),
            buses        = sorted(buses),
            shunts       = sorted(shunt),
            transformers = sorted(trafo),
            external_grd = sorted(external_grids),
            resolution   = GenericAsset.query.get(batteries[0]).sensors[0].event_resolution,
            belief_time  = now
        )

        success = False
        if OPForPF == 'OPF':
            success = eflex_opf(**scheduling_kwargs)
        elif OPForPF == 'PF':
            success = eflex_pf(**scheduling_kwargs)

        if success:
            print("PF done with success")
        else:
            print("ERROR!")

        return render_flexmeasures_template(
            "admin/opf.html",
            logged_in_user=current_user,
            networks=networks,
        )
