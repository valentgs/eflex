from ast import List
from datetime import datetime, timedelta

import click
from numpy import single
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

def eflex_opf(
    start: datetime,
    end: datetime,
    load: int,
    battery: int,
    shunts: int,
    transformers: int,
    buses: int,
    lines: int, 
    external_grd: int,
    resolution: timedelta,
    belief_time: datetime,
    flex_config_has_been_deserialized: bool = False,
) -> bool:
    """
    This function computes an opf. It returns True if it ran successfully.

    This is what this function does:
    - Turn results values into beliefs and save them to db
    """
    # https://docs.sqlalchemy.org/en/13/faq/connections.html#how-do-i-use-engines-connections-sessions-with-python-multiprocessing-or-os-fork
    battery_sensors_w = []
    battery_sensors_var = []
    for eg in external_grd:
        battery.append(eg)
    for bt in battery:
        for sens in GenericAsset.query.get(bt).sensors:
            if sens.unit == "W":
                battery_sensors_w.append(sens.id)
            if sens.unit == "VAr":
                battery_sensors_var.append(sens.id)
        

    # Closing all active connections and releasing resources
    db.engine.dispose()

    rq_job = get_current_job()
    if rq_job:
        click.echo(
            "Running Scheduling Job %s: %s, from %s to %s"
            % (rq_job.id, battery_sensors_w[0], start, end)
        )
    
    data_source_info = StorageScheduler.get_data_source_info()

    if belief_time is None:
        belief_time = server_now()

    nbr_ld = len(load)
    load_data = {"Active": [], "Reactive": []}
    if nbr_ld > 0:
        for ld in load:
            for sens in GenericAsset.query.get(ld).sensors:
                # Getting only W and VAr information
                if sens.unit == "W":
                    load_data.get("Active").append(list(sens.search_beliefs(start, end).get("event_value").values))
                if sens.unit == "VAr":
                    load_data.get("Reactive").append(list(sens.search_beliefs(start, end).get("event_value").values))
    # print(load_data)

    # Getting the Number of batteries
    nbr_bt = len(battery)
    
    # If there is no load, go for the interval
    nbr_blfs = len(load_data.get("Active")[0])
    if nbr_blfs == 0:
        nbr_blfs = (end - start) / resolution

    # Creating a list to save results that is going to be saved on the db
    p_w_results = []
    p_var_results = []
    for _ in range(nbr_bt):
        p_w_results.append([])
        p_var_results.append([])

    ####################################################################################################################
    # Here the optimization begins
    for j in range(int(nbr_blfs)):
        # Creating the network
        net = pp.create_empty_network()

        # Creating the network buses 
        for bus in buses:
            vn = NetworkResource.query.get(bus).get_attribute("vn_kv")
            vmax = NetworkResource.query.get(bus).get_attribute("max_vm_pu")
            vmin = NetworkResource.query.get(bus).get_attribute("min_vm_pu")
            print("bus", bus, vn, vmax, vmin)
            pp.create_bus(net, index=bus, vn_kv=vn, max_vm_pu=vmax, min_vm_pu=vmin, zone=1)

        # Creating network lines
        for line in lines:
            fbus = NetworkResource.query.get(line).get_attribute("from_bus")
            tbus = NetworkResource.query.get(line).get_attribute("to_bus")
            l = NetworkResource.query.get(line).get_attribute("length_km")
            r = NetworkResource.query.get(line).get_attribute("r_ohm_per_km")
            x = NetworkResource.query.get(line).get_attribute("x_ohm_per_km")
            c = NetworkResource.query.get(line).get_attribute("c_nf_per_km")
            i = NetworkResource.query.get(line).get_attribute("max_i_ka")
            print("line", line, fbus, tbus, l, r, x, c, i)
            pp.create_line_from_parameters(net, from_bus=fbus, to_bus=tbus, length_km=l, r_ohm_per_km=r, x_ohm_per_km=x, c_nf_per_km=c, max_i_ka=i,type="ol", max_loading_percent=100.0)
        
        # Creating network loads
        if nbr_ld > 0:
            for i, ld in enumerate(load):
                b = GenericAsset.query.get(ld).get_attribute("bus")
                p = load_data.get("Active")[i][j]
                q = load_data.get("Reactive")[i][j]
                print("load", ld, b, p, q)
                pp.create_load(net, bus=b, p_mw=p, q_mvar=q, type=None)

        # Creating the batteries (generators)
        for bt in battery:
            b = GenericAsset.query.get(bt).get_attribute("bus")
            p = GenericAsset.query.get(bt).get_attribute("p_mw")
            vm = GenericAsset.query.get(bt).get_attribute("vm_pu")
            qmax = GenericAsset.query.get(bt).get_attribute("max_q_mvar")
            qmin = GenericAsset.query.get(bt).get_attribute("min_q_mvar")
            pmax = GenericAsset.query.get(bt).get_attribute("max_p_mw")
            pmin = GenericAsset.query.get(bt).get_attribute("min_p_mw")
            s = GenericAsset.query.get(bt).get_attribute("slack")
            s = s == 'True'
            print("battery", bt, b, p, vm, qmax, qmin, pmax, pmin, s)
            if s:
                pp.create_ext_grid(net, index=bt, bus=b, vm_pu=vm, p_mw=p, min_p_mw=pmin, max_p_mw=pmax, slack=s, max_q_mvar=qmax, min_q_mvar=qmin)
            else:
                pp.create_gen(net, index=bt, bus=b, vm_pu=vm, p_mw=p, min_p_mw=pmin, max_p_mw=pmax, slack=s, max_q_mvar=qmax, min_q_mvar=qmin, controllable=True)

        # Creating the polynomial costs
        for bt in battery:
            cp0 = GenericAsset.query.get(bt).get_attribute("cp0")
            cp1 = GenericAsset.query.get(bt).get_attribute("cp1")
            cp2 = GenericAsset.query.get(bt).get_attribute("cp2")
            cq0 = GenericAsset.query.get(bt).get_attribute("cq0")
            cq1 = GenericAsset.query.get(bt).get_attribute("cq1")
            cq2 = GenericAsset.query.get(bt).get_attribute("cq2")
            s = GenericAsset.query.get(bt).get_attribute("slack")
            s = s == 'True'
            if s:
                et_ = 'ext_grid'
            else: 
                et_ = 'gen'
            print("polynomial cost", cp0, cp1, cp2, cq0, cq1, cq2)
            pp.create_poly_cost(net, element=bt, et=et_, cp0_eur=cp0, cp1_eur_per_mw=cp1, cp2_eur_per_mw2=cp2, cq0_eur=cq0, cq1_eur_per_mw=cq1, cq2_eur_per_mw2=cq2)

        # Creating the transformers
        for transformer in transformers:
            hvb = NetworkResource.query.get(transformer).get_attribute("hv_bus")
            lvb = NetworkResource.query.get(transformer).get_attribute("lv_bus")
            sn = NetworkResource.query.get(transformer).get_attribute("sn_mva")
            vnhv = NetworkResource.query.get(transformer).get_attribute("vn_hv_kv")
            vnlv = NetworkResource.query.get(transformer).get_attribute("vn_lv_kv")
            vkp = NetworkResource.query.get(transformer).get_attribute("vk_percent")
            vkr = NetworkResource.query.get(transformer).get_attribute("vkr_percent")
            pfe = NetworkResource.query.get(transformer).get_attribute("pfe_kw")
            i0 = NetworkResource.query.get(transformer).get_attribute("i0_percent")           

            print("transformer", transformer, hvb, lvb, sn, vnhv, vnlv, vkp, vkr, pfe, i0)
            pp.create_transformer_from_parameters(net, hv_bus=hvb, lv_bus=lvb, sn_mva=sn, vn_hv_kv=vnhv, vn_lv_kv=vnlv, 
                                                  vk_percent=vkp, vkr_percent=vkr, pfe_kw=pfe, i0_percent=i0)

        # Creating the shunts
        for shunt in shunts:
            b = NetworkResource.query.get(shunt).get_attribute("bus")
            q = NetworkResource.query.get(shunt).get_attribute("q_mvar")
            p = NetworkResource.query.get(shunt).get_attribute("p_mw")
            v = NetworkResource.query.get(shunt).get_attribute("vn_kv")
            print("shunt", shunt, b, q, p, v)
            pp.create_shunt(net, bus=b, q_mvar=q, p_mw=p, vn_kv=v)
        
        print("\nNet:")
        print(net)

        print("\nNet Buses:")
        print(net.bus)

        print("\nNet Loads:")
        print(net.load)

        print("\nNet Generators:")
        print(net.gen)

        print("\nNet Shunt:")
        print(net.shunt)

        print("\nNet External Grid:")
        print(net.ext_grid)

        print("\nNet Lines:")
        print(net.line)

        print("\nNet Trafos:")
        print(net.trafo)

        print("\nNet Polynomial Costs:")
        print(net.poly_cost)

        # Run the optimal power flow
        pp.runopp(net, numba=False)

        print("\nBus voltages after OPF:")
        print(net.res_bus.vm_pu)

        print("\nActive power generation after OPF:")
        print(net.res_gen.p_mw)
        print(net.res_ext_grid)

        print("\nLoad shedding after OPF:")
        print(net.res_load.p_mw)

        print("\nLine loadings after OPF:")
        print(net.res_line.loading_percent)

        print(net.res_gen)
        # Output results
        # Arrumar isso daqui, botar as coisas no lugar certo
        print(len(net.gen) + len(net.ext_grid))
        for k in range(len(net.gen) + len(net.ext_grid)):
            if(k < len(net.gen)):
                p_w_results[k].append(net.res_gen.get("p_mw").values[k])
                p_var_results[k].append(net.res_gen.get("q_mvar").values[k])
            else:
                p_w_results[k].append(net.res_ext_grid.get("p_mw").values[k-len(net.gen)])
                p_var_results[k].append(net.res_ext_grid.get("q_mvar").values[k-len(net.gen)])
                
    ####################################################################################################################

    # Adding a the data into a data series
    bat_w = []
    bat_var = []

    for iter in range(nbr_bt):
        bat_w.append([])
        bat_var.append([])

    for iter in range(nbr_bt):
        bat_w[iter].append(initialize_series(data=p_w_results[iter], start=start, end=end, resolution=to_offset(resolution)))
        bat_var[iter].append(initialize_series(data=p_var_results[iter], start=start, end=end, resolution=to_offset(resolution)))
    
    if rq_job:
        click.echo("Job %s made schedule." % rq_job.id)

    # Creating a data source
    data_source = get_data_source(
        data_source_name=data_source_info["name"],
        data_source_model=data_source_info["model"],
        data_source_version=data_source_info["version"],
        data_source_type="scheduling script",
    )

    # Saving info on the job, so the API for a job can look the data up
    data_source_info["id"] = data_source.id
    if rq_job:
        rq_job.meta["data_source_info"] = data_source_info
        rq_job.save_meta()

    # Saving data to db
    for iter in range(len(bat_w)):
        ts_value_schedule = [
            TimedBelief(
                event_start=dt,
                belief_time=belief_time,
                event_value=value,
                sensor=Sensor.query.get(battery_sensors_w[iter]),
                source=data_source,
            )
            for dt, value in bat_w[iter][0].items()
        ]
        bdf = tb.BeliefsDataFrame(ts_value_schedule)
        save_to_db(bdf, bulk_save_objects=True)
    
    for iter in range(len(bat_var)):
        ts_value_schedule = [
            TimedBelief(
                event_start=dt,
                belief_time=belief_time,
                event_value=value,
                sensor=Sensor.query.get(battery_sensors_var[iter]),
                source=data_source,
            )
            for dt, value in bat_var[iter][0].items()
        ]
        bdf = tb.BeliefsDataFrame(ts_value_schedule)
        save_to_db(bdf, bulk_save_objects=True)
    
    # Commit the current transaction to the database
    db.session.commit()
    return True


def eflex_pf(
    start: datetime,
    end: datetime,
    load: int,
    battery: int,
    lines: List,
    buses: List,
    resolution: timedelta,
    belief_time: datetime,
    flex_config_has_been_deserialized: bool = False,
) -> bool:
    """
    This function computes a schedule. It returns True if it ran successfully.

    This is what this function does:
    - Find out which scheduler should be used & compute the schedule
    - Turn scheduled values into beliefs and save them to db
    """
    # https://docs.sqlalchemy.org/en/13/faq/connections.html#how-do-i-use-engines-connections-sessions-with-python-multiprocessing-or-os-fork
    # Closing all active connections and releasing resources

    # for ld in load:
    #     print("teste paizinhuuuuuuu", GenericAsset.query.get(ld).sensors)
    
    battery_sensors_w = []
    battery_sensors_var = []
    for bt in battery:
        for sens in GenericAsset.query.get(bt).sensors:
            if sens.unit == "W":
                battery_sensors_w.append(sens.id)
            if sens.unit == "VAr":
                battery_sensors_var.append(sens.id)
    # print(battery_sensors_w, battery_sensors_var)
    
    db.engine.dispose()

    rq_job = get_current_job()
    if rq_job:
        click.echo(
            "Running Scheduling Job %s: %s, from %s to %s"
            % (rq_job.id, battery[0], start, end)
        )
    
    data_source_info = StorageScheduler.get_data_source_info()

    if belief_time is None:
        belief_time = server_now()
    
    # Getting load data if there is load in the network
    nbr_ld = len(load)
    load_data = {"Active": [], "Reactive": []}
    if nbr_ld > 0:
        for ld in load:
            for sens in GenericAsset.query.get(ld).sensors:
                # Getting only W and VAr information
                if sens.unit == "W":
                    load_data.get("Active").append(list(sens.search_beliefs(start, end).get("event_value").values))
                if sens.unit == "VAr":
                    load_data.get("Reactive").append(list(sens.search_beliefs(start, end).get("event_value").values))
    # print(load_data)

    # Getting the Number of batteries
    nbr_bt = len(battery)

    # If there is no load, go for the interval
    nbr_blfs = len(load_data.get("Active")[0])
    if nbr_blfs == 0:
        nbr_blfs = (end - start) / resolution
    
    # Creating a list to save results that is going to be saved on the db
    p_w_results = []
    p_var_results = []
    for _ in range(nbr_bt):
        p_w_results.append([])
        p_var_results.append([])

    ####################################################################################################################
    # Here the optimization begins
    for j in range(int(nbr_blfs)):
        # Creating the network
        net = pp.create_empty_network()
        
        # Creating the network buses 
        for bus in buses:
            pp.create_bus(net, index=bus, vn_kv=NetworkResource.query.get(bus).get_attribute("vn_kv"))
        
        # Creating network lines
        for line in lines:
            fbus = NetworkResource.query.get(line).get_attribute("from_bus")
            tbus = NetworkResource.query.get(line).get_attribute("to_bus")
            l = NetworkResource.query.get(line).get_attribute("length_km")
            r = NetworkResource.query.get(line).get_attribute("r_ohm_per_km")
            x = NetworkResource.query.get(line).get_attribute("x_ohm_per_km")
            c = NetworkResource.query.get(line).get_attribute("c_nf_per_km")
            i = NetworkResource.query.get(line).get_attribute("max_i_ka")
            pp.create_line_from_parameters(net, from_bus=fbus, to_bus=tbus, length_km=l, r_ohm_per_km=r, x_ohm_per_km=x, c_nf_per_km=c, max_i_ka=i)

        # Creating network loads
        if nbr_ld > 0:
            for i, ld in enumerate(load):
                b = GenericAsset.query.get(ld).get_attribute("bus")
                p = load_data.get("Active")[i][j]
                q = load_data.get("Reactive")[i][j]
                pp.create_load(net, bus=b, p_mw=p, q_mvar=q)

        # Creating network batteries (generators)
        for bt in battery:
            b = GenericAsset.query.get(bt).get_attribute("bus")
            v = GenericAsset.query.get(bt).get_attribute("vm_pu")
            p = GenericAsset.query.get(bt).get_attribute("p_mw")
            s = GenericAsset.query.get(bt).get_attribute("slack")
            s = s == 'True'
            pp.create_gen(net, index=bt, bus=b, p_mw=p, vm_pu=v, slack=s, controllable=True)

        # Run the optimal power flow
        pp.runpp(net, numba=False)
        
        # Output results
        for k in range(nbr_bt):
            p_w_results[k].append(net.res_gen.get("p_mw").values[k])
            p_var_results[k].append(net.res_gen.get("q_mvar").values[k])
    ####################################################################################################################
    
    # Adding a the data into a data series
    bat_w = []
    bat_var = []
    for iter in range(nbr_bt):
        bat_w.append([])
        bat_var.append([])
    for iter in range(nbr_bt):
        bat_w[iter].append(initialize_series(data=p_w_results[iter], start=start, end=end, resolution=to_offset(resolution)))
        bat_var[iter].append(initialize_series(data=p_var_results[iter], start=start, end=end, resolution=to_offset(resolution)))
        
    if rq_job:
        click.echo("Job %s made schedule." % rq_job.id)

    # Creating a data source
    data_source = get_data_source(
        data_source_name=data_source_info["name"],
        data_source_model=data_source_info["model"],
        data_source_version=data_source_info["version"],
        data_source_type="scheduling script",
    )

    # Saving info on the job, so the API for a job can look the data up
    data_source_info["id"] = data_source.id
    if rq_job:
        rq_job.meta["data_source_info"] = data_source_info
        rq_job.save_meta()

    # Saving data to db
    for iter in range(len(bat_w)):
        ts_value_schedule = [
            TimedBelief(
                event_start=dt,
                belief_time=belief_time,
                event_value=value,
                sensor=Sensor.query.get(battery_sensors_w[iter]),
                source=data_source,
            )
            for dt, value in bat_w[iter][0].items()
        ]
        bdf = tb.BeliefsDataFrame(ts_value_schedule)
        save_to_db(bdf, bulk_save_objects=True)
    
    for iter in range(len(bat_var)):
        ts_value_schedule = [
            TimedBelief(
                event_start=dt,
                belief_time=belief_time,
                event_value=value,
                sensor=Sensor.query.get(battery_sensors_var[iter]),
                source=data_source,
            )
            for dt, value in bat_var[iter][0].items()
        ]
        bdf = tb.BeliefsDataFrame(ts_value_schedule)
        save_to_db(bdf, bulk_save_objects=True)
    
    # Commit the current transaction to the database
    db.session.commit()
    return True