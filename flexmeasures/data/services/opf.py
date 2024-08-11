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
    buses: int,
    lines: int, 
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
    # print()
    # print("start", start)
    # print("end", end)
    # print("load", load)
    # print("battery", battery)
    # print("buses", buses)
    # print("lines", lines)
    # print("resolution", resolution)
    # print("belief_time", belief_time)

    battery_sensors_w = []
    battery_sensors_var = []
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
            % (rq_job.id, battery_sensor[0], start, end)
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
            # print("line", fbus, tbus, l, r, x, c, i)
            pp.create_line_from_parameters(net, from_bus=fbus, to_bus=tbus, length_km=l, r_ohm_per_km=r, x_ohm_per_km=x, c_nf_per_km=c, max_i_ka=i)

        # Creating network loads
        if nbr_ld > 0:
            for i, ld in enumerate(load):
                b = GenericAsset.query.get(ld).get_attribute("bus")
                p = load_data.get("Active")[i][j]
                q = load_data.get("Reactive")[i][j]
                pp.create_load(net, bus=b, p_mw=p, q_mvar=q)
                
        # Creating the batteries (generators)
        for bt in battery:
            b = GenericAsset.query.get(bt).get_attribute("bus")
            pmax = GenericAsset.query.get(bt).get_attribute("p_max")
            pmin = GenericAsset.query.get(bt).get_attribute("p_min")
            s = GenericAsset.query.get(bt).get_attribute("slack")
            s = s == 'True'
            pp.create_gen(net, index=bt, bus=b, vm_pu=1.0, p_mw=p, min_p_mw=pmin, max_p_mw=pmax, slack=s, controllable=True)

        # Creating the polynomial costs
        cp0 = [GenericAsset.query.get(battery[i]).get_attribute("cp0")
            for i in range(nbr_bt)]
        cp1 = [GenericAsset.query.get(battery[i]).get_attribute("cp1")
            for i in range(nbr_bt)]
        cp2 = [GenericAsset.query.get(battery[i]).get_attribute("cp2")
            for i in range(nbr_bt)]
        nbr_et = len(cp0)*['gen']
        pp.create_poly_costs(net, elements=battery, et=nbr_et, cp0_eur=cp0, cp1_eur_per_mw=cp1, cp2_eur_per_mw2=cp2)
        
        # Run the optimal power flow
        pp.runopp(net, numba=False)

        # print(net.res_gen)
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
    # print()
    # print()
    # print("start", start)
    # print("end", end)
    # print("load_sensor", load)
    # print("battery_sensor", battery)
    # print("lines", lines)
    # print("buses", buses)
    # print("resolution", resolution)
    # print("belief_time", belief_time)

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