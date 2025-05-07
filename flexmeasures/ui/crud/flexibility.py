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
from flexmeasures.data.services.load_scheduling import eflex_load_scheduling
from datetime import datetime
from flexmeasures.utils.time_utils import server_now
from flexmeasures.ui.crud.networks import get_networks_by_account

"""
WRITE SOMETHING
"""

class RunFlexibilityUI(FlaskView):
    """
    These views help us offer a Jinja2-based UI.
    The main focus on logic is the API, so these views simply call the API functions,
    and deal with the response.
    Some new functionality, like fetching accounts and asset types, is added here.
    """

    route_base = "/flexibility"
    trailing_slash = False

    @login_required
    @route("/run")
    def run(self):
        """POST 
        """
        network_name = request.args.get('network_name')
        from_day = request.args.get('from_day')
        to_day = request.args.get('to_day')
        from_time = request.args.get('from_time')
        to_time = request.args.get('to_time')
        
        from_datetime = datetime.strptime(from_day + " " + from_time, "%Y-%m-%d %H:%M")
        to_datetime = datetime.strptime(to_day + " " + to_time, "%Y-%m-%d %H:%M")
        
        print("NETWORK NAME:", network_name)
        print("FROM:", from_datetime)
        print("TO:", to_datetime)

        networks = get_networks_by_account(current_user.account_id)
        the_network = Network.query.filter_by(name=network_name).first().id
        
        # lines_on_network, buses_on_network, shunts_on_network, transformers_on_network = [], [], [], []    
        # for resource in Network.query.get(the_network).network_resources:
        #     if(NetworkResource.query.get(resource).network_resource_type_id == 1):
        #         buses_on_network.append(NetworkResource.query.get(resource).id)
        #     if(NetworkResource.query.get(resource).network_resource_type_id == 0):
        #         lines_on_network.append(NetworkResource.query.get(resource).id)
        #     if(NetworkResource.query.get(resource).network_resource_type_id == 4):
        #         shunts_on_network.append(NetworkResource.query.get(resource).id)
        #     if(NetworkResource.query.get(resource).network_resource_type_id == 2):
        #         transformers_on_network.append(NetworkResource.query.get(resource).id)
        
        # battery = []
        # for bat in GenericAsset.query.filter_by(generic_asset_type_id=5).all():
        #     if bat.get_attribute("bus") in buses_on_network:
        #         battery.append(bat.id)

        # ext_grid = []
        # for battery_id in battery:
        #     battery_asset = GenericAsset.query.get(battery_id)
        #     if "external grid" in battery_asset.name.lower():
        #         ext_grid.append(battery_id)
        # battery = [b for b in battery if b not in ext_grid]
        
        # duration = to_datetime - from_datetime

        # import pytz
        # timezone = pytz.FixedOffset(180)  # 180 minutes = 3 hours
        
        # from_datetime = from_datetime.astimezone(timezone)
        # to_datetime = to_datetime.astimezone(timezone)
        
        # now_ = datetime.now()  # Example server time
        # now = now_.astimezone(timezone)

        # load_scheduling_kwargs = dict(
        #     start         = from_datetime,
        #     end           = from_datetime + duration,
        #     battery       = battery,
        #     lines         = lines_on_network,
        #     buses         = buses_on_network,
        #     shunts        = shunts_on_network,
        #     transformers  = transformers_on_network,
        #     external_grid = ext_grid,
        #     resolution    = GenericAsset.query.get(ext_grid[0]).sensors[0].event_resolution,
        #     belief_time   = server_now(),
        # )

        # success = eflex_load_scheduling(**load_scheduling_kwargs)
        # if success:
        #     print("Load Scheduling done with success")
        
        return render_flexmeasures_template(
            "admin/flexibility.html",
            logged_in_user=current_user,
            networks=networks,
        )

    # @login_required
    # @route("/run", methods=["POST"])
    # def run(self):
    #     """POST to 
    #     """
    #     data = request.get_json()
    #     state = data.get("state")
    #     network = data.get("network")
    #     print("DATA:", data)
    #     print("NETWORK NAME:", network)
    #     print("STATE:", state)
        
    #     networks = get_networks_by_account(current_user.account_id)
               
    #     return render_flexmeasures_template(
    #         "admin/flexibility.html",
    #         logged_in_user=current_user,
    #         networks=networks,
    #     )
