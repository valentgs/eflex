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

def eflex_flexibility(
    start: datetime,
    end: datetime,
    battery: int,
    shunts: int,
    transformers: int,
    buses: int,
    lines: int, 
    external_grid: int,
    pvs: int,
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
    network = build_network(network, buses, lines, external_grid, battery, transformers, shunts, pvs)
    print(network)
    
    load_data = {}
    gen_data = {}
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
        
        pv_assets = GenericAsset.query.filter_by(generic_asset_type_id=1).all()
        for pv in pv_assets:
            if pv.get_attribute("bus") == bus:
                asset_name = pv.name
                active_power = []
                for sens in pv.sensors:
                    if sens.unit == "W":
                        beliefs = sens.search_beliefs(start, end, source="thesis")
                        active_power = list(beliefs.get("event_value").values)
                gen_data[asset_name] = {"Active Power": active_power}
                

    h, sc, hoc, oc, tc, flexibility_results, p_bat, bat_socs, dump = [], [], [], [], [], [], [], [], []
    for K in range(len(next(iter(load_data.values()))["Active Power"])):
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! DEBUGGING SESSION !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        # h.append(K), sc.append(solar_curve[K]), hoc.append(house_curve[K]), oc.append(office_curve[K]), tc.append(total_curve[K])
        
        # Getting the load at hour h
        L_max = []
        for index, bus in network.load.iterrows():
            L_max.append(load_data[list(load_data.keys())[index]]['Active Power'][K])
            network.load.loc[network.load['bus'] == network.load['bus'][index], 'p_mw'] =   load_data[list(load_data.keys())[index]]['Active Power'][K]

        gen = []
        for index, bus in network.gen.iterrows():
            gen.append(gen_data[list(gen_data.keys())[index]]['Active Power'][K])
            network.gen.loc[network.gen['bus'] == network.gen['bus'][index], 'p_mw'] =       gen_data[list(gen_data.keys())[index]]['Active Power'][K]
            network.gen.loc[network.gen['bus'] == network.gen['bus'][index], 'min_p_mw'] =   gen_data[list(gen_data.keys())[index]]['Active Power'][K]
            network.gen.loc[network.gen['bus'] == network.gen['bus'][index], 'max_p_mw'] =   gen_data[list(gen_data.keys())[index]]['Active Power'][K]

        pp.runopp(network, verbose=False)

        prices_load, prices_battery = [], []
        batteries = {}
        for load in network.load.index:
            prices_load.append(network.res_bus.at[network.load.at[load, 'bus'], 'lam_p'])
        for bt in network.storage.index:
            prices_battery.append(network.res_bus.at[network.storage.at[bt, 'bus'], 'lam_p'])
            batteries[f'Battery {bt}'] = {'bf_min': network.storage.at[bt, 'min_p_mw'], 
                                        'bf_max': network.storage.at[bt, 'max_p_mw'],
                                        'soc_min': network.storage.at[bt, 'min_e_mwh'] * 100 / network.storage.at[bt, 'max_e_mwh'], 
                                        'soc_max': network.storage.at[bt, 'max_e_mwh'] * 100 * 0.9 / network.storage.at[bt, 'max_e_mwh'],
                                        'soc_now': network.storage.at[bt, 'soc_percent'], 
                                        'e_nom': network.storage.at[bt, 'max_e_mwh']}
        dt = 1
        l_flex, batc_flex, batd_flex, soc_flex, dump_flex = flexibility(L_max, np.array(prices_load), gen, np.array(prices_battery), batteries, dt)
        
        for i, bt in enumerate(network.storage.index):
            network.storage.at[bt, 'soc_percent'] = soc_flex[i]
        
        p_bat.append(batc_flex - batd_flex)
        bat_socs.append(soc_flex)
        dump.append(dump_flex)
        flexibility_results.append(l_flex)

    # Transform p_bat into a list of lists
    p_bat = [list(v) for v in zip(*p_bat)]
    flexibility_results = [list(v) for v in zip(*flexibility_results)]
    bat_socs = [list(v) for v in zip(*bat_socs)]

    # Save the information of p_bat in the sensors of the batteries
    belief_time = belief_time or server_now()
    data_source = get_data_source(data_source_name="scheduler", data_source_type="scheduler")

    for i, battery in enumerate(battery):
        asset = GenericAsset.query.get(battery)
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
                        for i, value in enumerate(p_bat[i])
                    ]
                    bdf = tb.BeliefsDataFrame(ts_value_schedule)
                    save_to_db(bdf, bulk_save_objects=True)
                if sensor.unit == "%":
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
                        for i, value in enumerate(bat_socs[i])
                    ]
                    bdf = tb.BeliefsDataFrame(ts_value_schedule)
                    save_to_db(bdf, bulk_save_objects=True)
                
    
    # Save the information of flexibility_results in the load power sensors (only active)
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
                        for i, value in enumerate(flexibility_results[list(load_data.keys()).index(asset_name)])
                    ]
                    bdf = tb.BeliefsDataFrame(ts_value_schedule)
                    save_to_db(bdf, bulk_save_objects=True)

    db.session.commit()
    return False


def build_network(network, buses, lines, external_grid, battery, transformers, shunts, pvs):
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
        q = GenericAsset.query.get(bt).get_attribute("q_mvar")
        qmax = GenericAsset.query.get(bt).get_attribute("max_q_mvar")
        qmin = GenericAsset.query.get(bt).get_attribute("min_q_mvar")
        pmax = GenericAsset.query.get(bt).get_attribute("max_p_mw")
        pmin = GenericAsset.query.get(bt).get_attribute("min_p_mw")
        emin = GenericAsset.query.get(bt).get_attribute("min_e_mwh")
        emax = GenericAsset.query.get(bt).get_attribute("max_e_mwh")
        soc = GenericAsset.query.get(bt).get_attribute("soc_percent")
        pp.create_storage(network, index=bt, bus=bus, p_mw=p, q_mvar=q, 
                          min_p_mw=pmin, max_p_mw=pmax, max_q_mvar=qmax, 
                          min_q_mvar=qmin, soc_percent=soc, min_e_mwh=emin,
                          max_e_mwh=emax, controllable=True)


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


    for pv in pvs:
        bus = GenericAsset.query.get(pv).get_attribute("bus")
        pp.create_gen(network, bus, p_mw=0.0, vm_pu=1.0, name="Solar Generator", slack=True, controllable=False, min_p_mw=0.0, max_p_mw=0.0)

    return network

# import cvxpy as cp
# import numpy as np
# import matplotlib.pyplot as plt
 
def flexibility(ell_max, pl_i, g_i, pb_i, batts, delta_t): 
    # Define decision variable
    ell      = cp.Variable(len(pl_i))
    bf_dis   = cp.Variable(len(batts.values()))
    bf_cha   = cp.Variable(len(batts.values()))
    ebat_new = cp.Variable(len(batts.values()))
    dumped   = cp.Variable(len(batts.values()))

    a1 = 50*max(max(pl_i), max(pb_i))
    a2 = 40*max(max(pl_i), max(pb_i))
    a3 = 1*max(max(pl_i), max(pb_i))
    # Define objective function
    objective = cp.Minimize(pl_i @ ell + pb_i @ (a3 * bf_cha + bf_dis) + a1*cp.sum(ell_max - ell) + a2 * pb_i @ dumped) 

    # Define constraints
    constraints = []
    constraints.append(cp.sum(g_i) + cp.sum(bf_dis) == cp.sum(ell) + cp.sum(bf_cha) + cp.sum(dumped))
    constraints.append(ell >= 0)
    constraints.append(ell <= ell_max)
    for i, battery in enumerate(batts.values()):
        # print(i, battery)
        constraints.append(bf_cha[i] >= 0)
        constraints.append(bf_cha[i] <= battery['bf_max'])
        constraints.append(bf_dis[i] >= 0)
        constraints.append(bf_dis[i] <= -battery['bf_min'])
        constraints.append(ebat_new[i] == battery['e_nom'] * battery['soc_now'] / 100 + (bf_cha[i] - bf_dis[i]) * delta_t)
        constraints.append(ebat_new[i] >= battery['e_nom'] * battery['soc_min'] / 100)
        constraints.append(ebat_new[i] <= battery['e_nom'] * battery['soc_max'] / 100)
        
    constraints.append(dumped >= 0)
    # Solve problem
    problem = cp.Problem(objective, constraints)
    problem.solve()

    # Print results
    if problem.status == cp.OPTIMAL:
        print("Problem solved successfully.")
        print(f"Optimal cost: {problem.value:.2f}")
        print(f"Optimal ell: {np.round(ell.value, 2)}")
        print(f"Optimal bf_dis: {np.round(bf_dis.value, 2)}")
        print(f"Optimal bf_cha:, {np.round(bf_cha.value, 2)}")
        print(f"Dumped Energy: {np.round(dumped.value, 2)}")
        print(f"New SOC: {np.round((ebat_new.value / battery['e_nom']) * 100, 2)}") 
        return ell.value, bf_cha.value, bf_dis.value, (ebat_new.value / battery['e_nom']) * 100, dumped.value
    else:
        print('Problem Failed:', problem.status)
        print('Solver error message:', problem.solver_stats)    
        return None

