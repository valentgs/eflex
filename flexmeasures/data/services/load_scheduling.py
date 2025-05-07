from ast import List
from datetime import datetime, timedelta
from json import load

import click
import numpy as np
import cvxpy as cp
from numpy import single, source
from flexmeasures.data.models.network_resources import NetworkResource
from rq import get_current_job
import pandapower as pp
import timely_beliefs as tb

from flexmeasures.data import db
from flexmeasures.data.models.planning.storage import StorageScheduler
from flexmeasures.data.models.generic_assets import GenericAsset
from flexmeasures.data.models.time_series import Sensor, TimedBelief
from flexmeasures.data.models.planning.utils import initialize_series
from flexmeasures.data.utils import get_data_source, save_to_db
from flexmeasures.utils.time_utils import server_now
from pandas.tseries.frequencies import to_offset

def eflex_load_scheduling(
    start: datetime,
    end: datetime,
    battery: int,
    shunts: int,
    transformers: int,
    buses: int,
    lines: int, 
    external_grid: int,
    resolution: timedelta,
    belief_time: datetime,
    flex_config_has_been_deserialized: bool = False,
) -> bool:
    """
    This function computes a load scheduling. It returns True if it ran successfully.

    This is what this function does:
    - Turn results values into beliefs and save them to db
    """

    
    # Initialize an empty network using pandapower
    network = pp.create_empty_network()
    network = build_network(network, buses, lines, external_grid, battery, transformers, shunts)
    # Print all network tables for debugging purposes
    # print("Buses:")
    # print(network.bus)
    # print("\nLines:")
    # print(network.line)
    # print("\nLoads:")
    # print(network.load)
    # print("\nStatic Generators:")
    # print(network.sgen)
    # print("\nExternal Grids:")
    # print(network.ext_grid)
    # print("\nPoly Costs:")
    # print(network.poly_cost)

    load_data = {}
    for bus in buses:
        loads = GenericAsset.query.filter_by(generic_asset_type_id=6).all()
        for load_asset in loads:
            if load_asset.get_attribute("bus") == bus:
                asset_name = load_asset.name
                active_power = []
                reactive_power = []
                for sens in load_asset.sensors:
                    if sens.unit == "W":
                        beliefs = sens.search_beliefs(start, end, source="thesis")
                        active_power = list(beliefs.get("event_value").values)
                    if sens.unit == "VAr":
                        beliefs = sens.search_beliefs(start, end, source="thesis")
                        reactive_power = list(beliefs.get("event_value").values)
                load_data[asset_name] = {"Active Power": active_power, "Reactive Power": reactive_power, "Type": load_asset.get_attribute("type")}
                
    # print(load_data)
    tc = []
    tc.append(get_first_cost(network, load_data, None))
    for iterator in range(1, 21):
        nlc, nnw, ntc, npr = schedule_N(network, load_data, None)
        load_data = nlc
        network = nnw
        tc.append(ntc)
        print(iterator, ntc)    
        # if (iterator % 1 == 0):
        #     print(iterator, npr, nlc, nnw)

    # Save the information from nlc to the sensors without overwriting current curves
    belief_time = belief_time or server_now()
    data_source = get_data_source(data_source_name="scheduler", data_source_type="scheduler")

    for asset_name, load_info in load_data.items():
        asset = GenericAsset.query.filter_by(name=asset_name).first()
        if asset:
            for sensor in asset.sensors:
                if sensor.unit == "W":
                    # Delete existing beliefs for the given time range only if the source is "scheduling"
                    TimedBelief.query.filter(
                        TimedBelief.sensor_id == sensor.id,
                        TimedBelief.event_start >= start,
                        TimedBelief.event_start < end,
                        TimedBelief.source == data_source,
                    ).delete(synchronize_session=False)

                    # Insert new beliefs
                    ts_value_schedule = [
                        TimedBelief(
                            event_start=start + i * resolution,
                            belief_time=belief_time,
                            event_value=value,
                            sensor=sensor,
                            source=data_source,
                        )
                        for i, value in enumerate(load_info["Active Power"])
                    ]
                    bdf = tb.BeliefsDataFrame(ts_value_schedule)
                    save_to_db(bdf, bulk_save_objects=True)

                elif sensor.unit == "VAr":
                    # Delete existing beliefs for the given time range only if the source is "scheduling"
                    TimedBelief.query.filter(
                        TimedBelief.sensor_id == sensor.id,
                        TimedBelief.event_start >= start,
                        TimedBelief.event_start < end,
                        TimedBelief.source == data_source,
                    ).delete(synchronize_session=False)

                    # Insert new beliefs
                    ts_value_schedule = [
                        TimedBelief(
                            event_start=start + i * resolution,
                            belief_time=belief_time,
                            event_value=value,
                            sensor=sensor,
                            source=data_source,
                        )
                        for i, value in enumerate(load_info["Reactive Power"])
                    ]
                    bdf = tb.BeliefsDataFrame(ts_value_schedule)
                    save_to_db(bdf, bulk_save_objects=True)

    db.session.commit()
    return False
#     # https://docs.sqlalchemy.org/en/13/faq/connections.html#how-do-i-use-engines-connections-sessions-with-python-multiprocessing-or-os-fork
#     battery_sensors_w = []
#     battery_sensors_var = []
#     for eg in external_grd:
#         battery.append(eg)
#     for bt in battery:
#         for sens in GenericAsset.query.get(bt).sensors:
#             if sens.unit == "W":
#                 battery_sensors_w.append(sens.id)
#             if sens.unit == "VAr":
#                 battery_sensors_var.append(sens.id)
        

#     # Closing all active connections and releasing resources
#     db.engine.dispose()

#     rq_job = get_current_job()
#     if rq_job:
#         click.echo(
#             "Running Scheduling Job %s: %s, from %s to %s"
#             % (rq_job.id, battery_sensors_w[0], start, end)
#         )
    
#     data_source_info = StorageScheduler.get_data_source_info()

#     if belief_time is None:
#         belief_time = server_now()

#     nbr_ld = len(load)
#     load_data = {"Active": [], "Reactive": []}
#     if nbr_ld > 0:
#         for ld in load:
#             for sens in GenericAsset.query.get(ld).sensors:
#                 # Getting only W and VAr information
#                 if sens.unit == "W":
#                     load_data.get("Active").append(list(sens.search_beliefs(start, end).get("event_value").values))
#                 if sens.unit == "VAr":
#                     load_data.get("Reactive").append(list(sens.search_beliefs(start, end).get("event_value").values))
#     # print(load_data)

#     # Getting the Number of batteries
#     nbr_bt = len(battery)
    
#     # If there is no load, go for the interval
#     nbr_blfs = len(load_data.get("Active")[0])
#     if nbr_blfs == 0:
#         nbr_blfs = (end - start) / resolution

#     # Creating a list to save results that is going to be saved on the db
#     p_w_results = []
#     p_var_results = []
#     for _ in range(nbr_bt):
#         p_w_results.append([])
#         p_var_results.append([])

#     ####################################################################################################################

#         # Output results
#         # Arrumar isso daqui, botar as coisas no lugar certo
#         print(len(net.gen) + len(net.ext_grid))
#         for k in range(len(net.gen) + len(net.ext_grid)):
#             if(k < len(net.gen)):
#                 p_w_results[k].append(net.res_gen.get("p_mw").values[k])
#                 p_var_results[k].append(net.res_gen.get("q_mvar").values[k])
#             else:
#                 p_w_results[k].append(net.res_ext_grid.get("p_mw").values[k-len(net.gen)])
#                 p_var_results[k].append(net.res_ext_grid.get("q_mvar").values[k-len(net.gen)])
                
#     ####################################################################################################################

#     # Adding a the data into a data series
#     bat_w = []
#     bat_var = []

#     for iter in range(nbr_bt):
#         bat_w.append([])
#         bat_var.append([])

#     for iter in range(nbr_bt):
#         bat_w[iter].append(initialize_series(data=p_w_results[iter], start=start, end=end, resolution=to_offset(resolution)))
#         bat_var[iter].append(initialize_series(data=p_var_results[iter], start=start, end=end, resolution=to_offset(resolution)))
    
#     if rq_job:
#         click.echo("Job %s made schedule." % rq_job.id)

#     # Creating a data source
#     data_source = get_data_source(
#         data_source_name=data_source_info["name"],
#         data_source_model=data_source_info["model"],
#         data_source_version=data_source_info["version"],
#         data_source_type="scheduling script",
#     )

#     # Saving info on the job, so the API for a job can look the data up
#     data_source_info["id"] = data_source.id
#     if rq_job:
#         rq_job.meta["data_source_info"] = data_source_info
#         rq_job.save_meta()

#     # Saving data to db
#     for iter in range(len(bat_w)):
#         ts_value_schedule = [
#             TimedBelief(
#                 event_start=dt,
#                 belief_time=belief_time,
#                 event_value=value,
#                 sensor=Sensor.query.get(battery_sensors_w[iter]),
#                 source=data_source,
#             )
#             for dt, value in bat_w[iter][0].items()
#         ]
#         bdf = tb.BeliefsDataFrame(ts_value_schedule)
#         save_to_db(bdf, bulk_save_objects=True)
    
#     for iter in range(len(bat_var)):
#         ts_value_schedule = [
#             TimedBelief(
#                 event_start=dt,
#                 belief_time=belief_time,
#                 event_value=value,
#                 sensor=Sensor.query.get(battery_sensors_var[iter]),
#                 source=data_source,
#             )
#             for dt, value in bat_var[iter][0].items()
#         ]
#         bdf = tb.BeliefsDataFrame(ts_value_schedule)
#         save_to_db(bdf, bulk_save_objects=True)
    
#     # Commit the current transaction to the database
#     db.session.commit()
#     return True


def schedule_N(nw, loads, solars):
    new_loads = loads
    loads = list(loads.keys())
    for ld in loads:
        if new_loads[ld]['Type'] == 'Inflexible': continue
        
        total_cost = 0.0
        prices = create_empty_price_curves(nw)

        # Get the bus in the network to which the load is connected
        # bus_ld = int(nw.load.loc[nw.load['bus'] == GenericAsset.query.filter_by(name=ld).first().get_attribute("bus"), 'bus'].values[0])
        bus_ld = int(nw.load.loc[nw.load['bus'] == GenericAsset.query.filter_by(name=ld).first().get_attribute("bus"), 'bus'].index[0])
        
        # Run OPF for every hour to get prices and check the power flow    
        for hour in range(len(new_loads[ld]['Active Power'])):

            # Getting the load at hour h 
            for index, bus in nw.load.iterrows():
                nw.load.loc[nw.load['bus'] == nw.load['bus'][index], 'p_mw'] =   new_loads[loads[index]]["Active Power"][hour]
                nw.load.loc[nw.load['bus'] == nw.load['bus'][index], 'q_mvar'] = new_loads[loads[index]]["Reactive Power"][hour] 
            
            # Getting the pv curve at hour h
            if solars is not None:
                for index, s in enumerate(solars):
                    nw.sgen.loc[nw.sgen['bus'] == nw.sgen['bus'][index], 'p_mw'] =   solars[index][hour]
                    nw.sgen.loc[nw.sgen['bus'] == nw.sgen['bus'][index], 'q_mvar'] = 0.0

            pp.runopp(nw, verbose=False, tolerance_mva=1e-6)
            total_cost += nw.res_cost
            # print(nw.res_bus['lam_p'])
            for i in range(len(nw.res_bus['lam_p'])):
                prices[i][0].append(nw.res_bus['lam_p'][nw.bus.index[i]])
                prices[i][1].append(nw.res_bus['lam_q'][nw.bus.index[i]])
        
        # Load Scheduling Part
        T = len(new_loads[ld]["Active Power"])
        p_before = new_loads[ld]["Active Power"]
        q_before = new_loads[ld]["Reactive Power"]
        price_p_at_bus = prices[bus_ld][0]
        price_q_at_bus = prices[bus_ld][1]
        if new_loads[ld]['Type'] == 'Breakable':
            # print("Breakable")
            p = breakload(T, price_p_at_bus, p_before)
            q = breakload(T, price_q_at_bus, q_before)
            new_loads[ld]["Active Power"] = p
            new_loads[ld]["Reactive Power"] = q
        elif new_loads[ld]['Type'] == 'Shiftable':
            # print("Shiftable")
            p = shiftload(T, price_p_at_bus, p_before)
            q = shiftload(T, price_q_at_bus, q_before)
            new_loads[ld]["Active Power"] = p
            new_loads[ld]["Reactive Power"] = q
        elif new_loads[ld]['Type'] == 'Modulatable':
            # print("Modulatable")
            p = modulateload(T, price_p_at_bus, p_before, min_power=0.0, max_power=48.0, max_change=1.0)
            q = modulateload(T, price_q_at_bus, q_before, min_power=0.0, max_power=48.0, max_change=1.0)
            new_loads[ld]["Active Power"] = p
            new_loads[ld]["Reactive Power"] = q

    return new_loads, nw, total_cost, prices


def build_network(network, buses, lines, external_grid, battery, transformers, shunts):
    # Create buses in the network
    for bus in buses:
        vn = NetworkResource.query.get(bus).get_attribute("vn_kv")
        vmin = NetworkResource.query.get(bus).get_attribute("min_vm_pu")
        vmax = NetworkResource.query.get(bus).get_attribute("max_vm_pu")
        pp.create_bus(network, index=bus, vn_kv=vn, max_vm_pu=vmax, min_vm_pu=vmin, zone=1)


    # Create lines in the network
    for line in lines:
        fbus = NetworkResource.query.get(line).get_attribute("from_bus")
        tbus = NetworkResource.query.get(line).get_attribute("to_bus")
        length = NetworkResource.query.get(line).get_attribute("length_km")
        r = NetworkResource.query.get(line).get_attribute("r_ohm_per_km")
        x = NetworkResource.query.get(line).get_attribute("x_ohm_per_km")
        c = NetworkResource.query.get(line).get_attribute("c_nf_per_km")
        max_i = NetworkResource.query.get(line).get_attribute("max_i_ka")
        pp.create_line_from_parameters(network, from_bus=fbus, to_bus=tbus, length_km=length, r_ohm_per_km=r, 
                                       x_ohm_per_km=x, c_nf_per_km=c, max_i_ka=max_i, type="None")


    # Create loads in the network
    for bus in buses:
        buildings = GenericAsset.query.filter_by(generic_asset_type_id = 6).all()
        for building in buildings:
            if building.get_attribute("bus") == bus:
                p = 30.0
                q = 30.0
                pp.create_load(network, bus=bus, p_mw=p, q_mvar=q, type=None, controllable=False)
                

    # Create external grid in the network
    for eg in external_grid:
        bus = GenericAsset.query.get(eg).get_attribute("bus")
        vm = GenericAsset.query.get(eg).get_attribute("vm_pu")
        qmax = GenericAsset.query.get(eg).get_attribute("max_q_mvar")
        qmin = GenericAsset.query.get(eg).get_attribute("min_q_mvar")
        pmax = GenericAsset.query.get(eg).get_attribute("max_p_mw")
        pmin = GenericAsset.query.get(eg).get_attribute("min_p_mw")
        pp.create_ext_grid(network, index=eg, bus=bus, vm_pu=vm, min_p_mw=pmin, max_p_mw=pmax,
                            max_q_mvar=qmax, min_q_mvar=qmin)

        cp0 = GenericAsset.query.get(eg).get_attribute("cp0")
        cp1 = GenericAsset.query.get(eg).get_attribute("cp1")
        cp2 = GenericAsset.query.get(eg).get_attribute("cp2")
        cq0 = GenericAsset.query.get(eg).get_attribute("cq0")
        cq1 = GenericAsset.query.get(eg).get_attribute("cq1")
        cq2 = GenericAsset.query.get(eg).get_attribute("cq2")
        pp.create_poly_cost(network, element=eg, et="ext_grid", cp0_eur=cp0, cp1_eur_per_mw=cp1, cp2_eur_per_mw2=cp2, 
                            cq0_eur=cq0, cq1_eur_per_mvar=cq1, cq2_eur_per_mvar2=cq2)
        

    # Create generators in the network
    for bt in battery:
        bus = GenericAsset.query.get(bt).get_attribute("bus")
        p = GenericAsset.query.get(bt).get_attribute("p_mw")
        vm = GenericAsset.query.get(bt).get_attribute("vm_pu")
        qmax = GenericAsset.query.get(bt).get_attribute("max_q_mvar")
        qmin = GenericAsset.query.get(bt).get_attribute("min_q_mvar")
        pmax = GenericAsset.query.get(bt).get_attribute("max_p_mw")
        pmin = GenericAsset.query.get(bt).get_attribute("min_p_mw")
        slack = GenericAsset.query.get(bt).get_attribute("slack") == 'True'
        pp.create_gen(network, index=bt, bus=bus, vm_pu=vm, p_mw=p, min_p_mw=pmin, max_p_mw=pmax, slack=slack, 
                          max_q_mvar=qmax, min_q_mvar=qmin, controllable=True)
        
        cp0 = GenericAsset.query.get(bt).get_attribute("cp0")
        cp1 = GenericAsset.query.get(bt).get_attribute("cp1")
        cp2 = GenericAsset.query.get(bt).get_attribute("cp2")
        cq0 = GenericAsset.query.get(bt).get_attribute("cq0")
        cq1 = GenericAsset.query.get(bt).get_attribute("cq1")
        cq2 = GenericAsset.query.get(bt).get_attribute("cq2")
        pp.create_poly_cost(network, element=bt, et="gen", cp0_eur=cp0, cp1_eur_per_mw=cp1, cp2_eur_per_mw2=cp2, 
                            cq0_eur=cq0, cq1_eur_per_mvar=cq1, cq2_eur_per_mvar2=cq2)


    # Create transformers in the network
    for transformer in transformers:
        hv_bus = NetworkResource.query.get(transformer).get_attribute("hv_bus")
        lv_bus = NetworkResource.query.get(transformer).get_attribute("lv_bus")
        sn = NetworkResource.query.get(transformer).get_attribute("sn_mva")
        vn_hv = NetworkResource.query.get(transformer).get_attribute("vn_hv_kv")
        vn_lv = NetworkResource.query.get(transformer).get_attribute("vn_lv_kv")
        vk_percent = NetworkResource.query.get(transformer).get_attribute("vk_percent")
        vkr_percent = NetworkResource.query.get(transformer).get_attribute("vkr_percent")
        pfe_kw = NetworkResource.query.get(transformer).get_attribute("pfe_kw")
        i0_percent = NetworkResource.query.get(transformer).get_attribute("i0_percent")
        pp.create_transformer_from_parameters(network, hv_bus=hv_bus, lv_bus=lv_bus, sn_mva=sn, vn_hv_kv=vn_hv, 
                                              vn_lv_kv=vn_lv, vk_percent=vk_percent, vkr_percent=vkr_percent, 
                                              pfe_kw=pfe_kw, i0_percent=i0_percent)

    # Create shunts in the network
    for shunt in shunts:
        bus = NetworkResource.query.get(shunt).get_attribute("bus")
        q = NetworkResource.query.get(shunt).get_attribute("q_mvar")
        p = NetworkResource.query.get(shunt).get_attribute("p_mw")
        vn = NetworkResource.query.get(shunt).get_attribute("vn_kv")
        pp.create_shunt(network, bus=bus, q_mvar=q, p_mw=p, vn_kv=vn)

    return network


def get_first_cost(nw, ld, sl):
    total_cost = 0.0
    # Run OPF for every hour to get prices and check the power flow 
    new_loads = ld
    sf = list(ld.keys())
    for hour in range(len(ld[sf[0]]["Active Power"])):
        
        # Getting the load at hour h 
        for index, bus in nw.load.iterrows():
            nw.load.loc[nw.load['bus'] == nw.load['bus'][index], 'p_mw'] =   new_loads[sf[index]]["Active Power"][hour]
            nw.load.loc[nw.load['bus'] == nw.load['bus'][index], 'q_mvar'] = new_loads[sf[index]]["Reactive Power"][hour] 
        
        # Getting the pv curve at hour h
        if sl is not None:
            for index, s in enumerate(sl):
                nw.sgen.loc[nw.sgen['bus'] == nw.sgen['bus'][index], 'p_mw'] =   sl[index][hour]
                nw.sgen.loc[nw.sgen['bus'] == nw.sgen['bus'][index], 'q_mvar'] = 0.0

        pp.runopp(nw, verbose=False, tolerance_mva=1e-6)
        total_cost += nw.res_cost
    return total_cost


def modulateload(T, p_i, ell_i, max_change, min_power, max_power):
    p_i, ell_i = np.array(p_i), np.array(ell_i)
    
    # Decision variable
    delta_ell_i = cp.Variable(T)  # Modulatable load curve

    # Constraints
    constraints = []

    # Constraint 1: ∑Δℓ_i,j = 0 (energy balance over the horizon)
    constraints.append(cp.sum(delta_ell_i) == 0)

    # Constraint 2: ℓ_i + Δℓ_i ≥ 0
    constraints.append(ell_i + delta_ell_i >= 0)

    # Constraint 3: |Δℓ_i| ≤ Δℓ_i,max
    constraints.append(delta_ell_i <= max_change)
    constraints.append(delta_ell_i >= -max_change)
    

    # Constraint 4: ℓ_i,min ≤ ℓ_i ≤ ℓ_i,max
    constraints.append(ell_i + delta_ell_i >= min_power)
    constraints.append(ell_i + delta_ell_i <= max_power)

    # Objective function
    objective = cp.Minimize(p_i @ (delta_ell_i + ell_i))

    # # Problem definition
    problem = cp.Problem(objective, constraints)

    # # Solve the problem
    problem.solve()

    # Output results
    if(False):
        if problem.status == "optimal":
            print("Original curve:    ", ell_i)
            print("Delta_l curve:     ", delta_ell_i.value)
            print("New curve:         ", delta_ell_i + ell_i)
            print("Objective value:   ", problem.value)
        else:
            print("Problem status: ", problem.status)


    return delta_ell_i.value + ell_i

def shiftload(T, p_i, ell_i):
    p_i, ell_i = np.array(p_i), np.array(ell_i)
    

    # Decision variables
    delta_ell_i = cp.Variable(T)  # Shifted curve
    b_i = cp.Variable(T, boolean=True)  # Binary activation for shift

    # Constraints
    constraints = []

    # 1. Define all possible shifts of the original curve
    shifted_curves = np.array([np.roll(ell_i, j) for j in range(T)])

    # 2. Enforce that delta_ell_i is one of the shifted versions, activated by b_i
    constraints.append(delta_ell_i == shifted_curves.T @ b_i)

    # 3. Only one shift is active
    constraints.append(cp.sum(b_i) == 1)

    # 4. Non-negativity
    constraints.append(delta_ell_i >= 0)

    # Objective function
    objective = cp.Minimize(p_i @ delta_ell_i)

    # Problem definition
    problem = cp.Problem(objective, constraints)

    # Solve the problem
    problem.solve()

    # Output results
    if(False):
        if problem.status == "optimal":
            print("Original curve:    ", ell_i)
            print("Shifted curve:     ", np.round(delta_ell_i.value, 2))
            print("Objective value:   ", problem.value)
        else:
            print("Problem status: ", problem.status)


    return delta_ell_i.value


def breakload(T, p_i, ell_i):
    p_i, ell_i = np.array(p_i), np.array(ell_i)

    # Find non-zero values in the original load curve
    non_zero_indices = np.where(ell_i != 0)[0]
    non_zero_values = ell_i[non_zero_indices]

    # Decision variables
    delta_ell_i = cp.Variable(T)  # Adjustment to the load curve
    ell_i_prime = cp.Variable(T)  # Reshuffled load curve
    z = cp.Variable((T, len(non_zero_values)), boolean=True)  # Reshuffling indicators

    # Constraints
    constraints = []

    # 1. Reshuffling constraint: Link ell_i_prime with non-zero values of ell_i
    for t in range(T):
        constraints.append(ell_i_prime[t] == cp.sum(z[t, :] @ non_zero_values))

    # 2. Each non-zero value must be assigned exactly once
    for j in range(len(non_zero_values)):
        constraints.append(cp.sum(z[:, j]) == 1)

    # 3. Each time slot can have at most one assigned value
    for t in range(T):
        constraints.append(cp.sum(z[t, :]) <= 1)

    # 4. Link ell_i_prime with Delta_ell_i
    constraints.append(ell_i_prime == ell_i + delta_ell_i)

    # 5. Total change across the curve must sum to zero
    constraints.append(cp.sum(delta_ell_i) == 0)

    # Objective: Minimize cost
    objective = cp.Minimize(p_i @ (ell_i + delta_ell_i))

    # Problem definition
    problem = cp.Problem(objective, constraints)

    # Solve the problem
    problem.solve()

    if(False):
        if problem.status == "optimal":
            print("Optimal reshaped curve (ell_i_prime):", ell_i_prime.value)
            print("Optimal adjustments (Delta_ell_i):", delta_ell_i.value)
            print("Optimal cost:", problem.value)
        else:
            print("Problem status: ", problem.status)
    
    
    return ell_i_prime.value


def create_empty_price_curves(nw):
    prices = []
    for _ in range(len(nw.bus)):
        prices.append([[], []])
    return prices