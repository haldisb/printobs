from datetime import datetime
import os
from dateutil.parser import parse
import pandas as pd
import yaml
import dotenv
import requests
import numpy as np
from pkg_resources import resource_stream
import xarray as xr
import json
from math import floor
import sys
import scipy as sp

def load_yaml(name):
    return yaml.safe_load(
            resource_stream(__name__,name))

varstr_dict = load_yaml('variable_def.yaml')
insitu_dict = load_yaml('insitu_locations.yaml')

#def func(arg: arg_type, optarg: arg_type = default) -> return_type:

def parse_date(indate: str) -> datetime:
    """
    parsing input date
    """
    if isinstance(indate, datetime):
        return indate
    elif isinstance(indate, str):
        return parse(indate)
    else:
        print('Not able to parse input return as is')
        return indate

def make_frost_reference_time_period(
    sdate: datetime, edate: datetime) -> str:
    """
    create special time format for frost call
    """
    sdate = parse_date(sdate)
    edate = parse_date(edate)
    formatstr = '%Y-%m-%dT%H:%M:00.000Z'
    refstr = '{}/{}'.format(sdate.strftime(formatstr),
                            edate.strftime(formatstr))
    return refstr

def call_frost_api(
    sdate: datetime, edate: datetime,
    nID: str, v: str) -> 'requests.models.Response':
    """
    make frost api call
    """
    varstr_lst = list(varstr_dict.keys())
    varstr = ','.join(varstr_lst)
    dotenv.load_dotenv()
    client_id = os.getenv('CLIENT_ID', None)
    client_secret = os.getenv('CLIENT_SECRET', None)
    frost_reference_time = make_frost_reference_time_period(sdate, edate)
    if client_id is None:
        print("No Frost CLIENT_ID given!")

    if client_secret is None:
        print("No Frost CLIENT_SECRET given!")
    if v == 'v0':
        r = call_frost_api_v0(nID, varstr,
                              frost_reference_time,
                              client_id)
    elif v == 'v1':
        r = call_frost_api_v1(nID, varstr,
                              frost_reference_time,
                              client_id, client_secret)
        print('r.status_code:', r.status_code)
    if r.status_code == 200:
        return r
    else:
        try:
            print(r.json()['error'])
        except requests.exceptions.JSONDecodeError:
            print(r.status_code, r.text)

def call_frost_api_v0(
    nID: str, varstr: str, frost_reference_time: str, client_id: str)\
    -> 'requests.models.Response':
    """
    frost call, retrieve data from frost v0
    """
    ID = 'SN' + str(insitu_dict[nID]['ID'])
    endpoint = 'https://frost.met.no/observations/v0.jsonld'  # v0
    parameters = {
                'sources': ID,
                'elements': varstr,
                'referencetime': frost_reference_time,
                'timeoffsets': 'default',
                'levels': 'default'
                }
    return requests.get(endpoint, parameters, auth=(client_id, client_id))

def get_typeid(insitu_dict: dict, s: str) -> str:
    typeid = insitu_dict[s].get('typeids')
    return typeid

def call_frost_api_v1(
    nID: str, varstr: str, frost_reference_time: str,
    client_id: str, client_secret: str)\
    -> 'requests.models.Response':
    """
    frost call, retrieve data from frost v1
    """
    ID = insitu_dict[nID]['ID']
    # HB change to frost-rc:
    #endpoint = ' https://frost-rc.met.no/api/v1/obs/base/get?'
    endpoint = ' https://frost.met.no/api/v1/obs/base/get?'
    parameters = {
                'stationids': ID,
                'elementids': varstr,
                'time': frost_reference_time,
                'levels': 'all',
                'incobs': 'true',
                'sensors': '0,1,2,3,4,5,6',
                }

    typeid = get_typeid(insitu_dict, nID)
    if typeid is not None:
        parameters['typeids'] = str(typeid)

    header = { "client_id": client_id,
               "client_secret": client_secret,
               "audience":"ODA",
               "grant_type": "urn:ietf:params:oauth:grant-type:uma-ticket"}

    response = requests.post(\
                  ("https://login.met.no/auth/realms/External/"
                 + "protocol/openid-connect/token"),\
                data=header)

    r_tmp = json.loads(response.content)
    headers = {"Authorization": "Bearer " + r_tmp["access_token"]}

    r = requests.get(endpoint, parameters, headers=headers)
    print(r.url)
    return r

def get_frost_df(r: 'requests.models.Response',v: str)\
    -> 'pandas.core.frame.DataFrame':
    """
    retrieve frost data as pandas dataframe
    """
    if v == 'v0':
        return get_frost_df_v0(r)
    elif v == 'v1':
        return get_frost_df_v1(r)

def get_frost_df_v0(r: 'requests.models.Response')\
    -> 'pandas.core.frame.DataFrame':
    """
    create pandas dataframe from frost call for v0
    """
    varstr_lst = list(varstr_dict.keys())
    alias_lst = [varstr_dict[e]['alias'] for e in varstr_dict]
    df = pd.json_normalize(r.json()['data'],
                            ['observations'],
                            ['referenceTime'])
    df2 = df['referenceTime'].drop_duplicates().reset_index(drop=True)
    df2 = df2.to_frame()
    for v in varstr_lst:
        dftmp = df.loc[df['elementId'] == v]['value'].reset_index(drop=True).to_frame()
        dftmp = dftmp.rename(columns={ dftmp.columns[0]: varstr_dict[v]['alias'] })
        df2 = pd.concat([df2, dftmp.reindex(df2.index)], axis=1)
    # rename referenceTime to time
    df2 = df2.rename(columns={ 'referenceTime': 'time' })
    return df2

def get_frost_df_v1(r: 'requests.models.Response')\
    -> 'pandas.core.frame.DataFrame':
    """
    create pandas dataframe from frost call for v1
    """
    # empy sensor id lst
    sensor_id_lst = []
    # base df
    df = pd.json_normalize(r.json()['data']['tseries'])
    # df to be concatenated initialized with time
    """
    # location for stationary
    # dfc3 = pd.json_normalize(r.json()['data']['tseries'])['header.extra.station.latitude']
    # location for moving platform
    # dfc3 = pd.json_normalize(r.json()['data']['tseries'][0]['observations'][0]['body'])['lat']
    """
    # select time index, some ts have less than others
    # choose the one with most values
    no_of_ts = len(pd.json_normalize(r.json()['data']['tseries'][:]))
    no_of_ts = min(4, no_of_ts)
    lenlst = []
    for t in range(no_of_ts):
        lenlst.append(len(pd.json_normalize(r.json()\
                      ['data']['tseries'][t]['observations'])['time'].\
                             to_frame()))
    time_idx = lenlst.index(max(lenlst))
    dfc = pd.json_normalize(r.json()
      ['data']['tseries'][time_idx]['observations'])['time'].to_frame()
    dinfo = {'sensor': {}, 'level': {}, 'paramid': {},
             'geometric height': {}, 'masl': {}}
    for vn in varstr_dict:
        idx = np.array(df['header.extra.element.id']\
                [df['header.extra.element.id'] == vn].index.to_list())
        ###
        # key variables for frost:
        #print(df['header.extra.element.id'][idx])
        #print(df['header.id.paramid'][idx])
        #print(df['header.id.level'][idx])
        #print(df['header.id.sensor'][idx])
        ###
        sensors = df['header.id.sensor'][idx].values
        paramids = df['header.id.paramid'][idx].values
        levels = df['header.id.level'][idx].values
        #levels = df['header.id.level'][idx].values/100
        if len(sensors) != len(np.unique(sensors)):
            print("-> id.sensor was not unique " \
                    + "selecting according to variable_def.yaml")
            print("   affected variable: ", vn)
            # 1. prioritize according to paramid
            if len(np.unique(paramids)) > 1:
                print('multiple paramids (',
                        len(np.unique(paramids)), ')')
                print('paramids:',np.unique(paramids))
                idx = find_preferred(
                        idx,sensors,paramids,\
                        varstr_dict[vn]['prime_paramid'])
                sensors = df['header.id.sensor'][idx].values
                paramids = df['header.id.paramid'][idx].values
                levels = df['header.id.level'][idx].values
            # 2. prioritize according to level
            if len(np.unique(levels)) > 1:
                print('multiple levels (', len(np.unique(levels)), ')')
                print('unique(levels):',np.unique(levels))
                idx = find_preferred(
                        idx,sensors,levels,\
                        varstr_dict[vn]['prime_level'])
                sensors = df['header.id.sensor'][idx].values
                paramids = df['header.id.paramid'][idx].values
                levels = df['header.id.level'][idx].values
        for n, i in enumerate(idx):
            dftmp = pd.json_normalize(r.json()\
                        ['data']['tseries'][i]['observations'])\
                        ['body.value'].to_frame()
            vns = varstr_dict[vn]['alias'] + '_' \
                        + str(df['header.id.sensor'][i])
            dftmp = dftmp.rename(columns={ dftmp.columns[0]: vns }).\
                            astype(float)
            dftmp[vns] = dftmp[vns].mask(dftmp[vns] < 0, np.nan)
            dfc = pd.concat([dfc, dftmp.reindex(dfc.index)], axis=1)
            # sensor
            dinfo['sensor'][vns] = sensors[n]
            # level
            if levels[n] == 0:
                dinfo['level'][vns] = varstr_dict[vn]['default_level']/100
            else:
                dinfo['level'][vns] = levels[n]/100
            # paramid
            dinfo['paramid'][vns] = paramids[n]
            #print(df['header.extra.timeseries.geometry.level.value'][i])
    return dfc, dinfo

def find_preferred(idx, sensors, refs, pref):
    sensorsU = np.unique(sensors)
    preferred_idx = []
    for s in sensorsU:
        no = len(refs[sensors==s])
        idx_1 = idx[sensors==s]
        if no > 1:
            idx_2 = np.where(refs[sensors==s]==pref)
            idx_3 = idx_1[idx_2]
            preferred_idx.append(list(idx_3)[0])
        else:
            preferred_idx.append(list(idx_1)[0])
    return preferred_idx

def get_element_id_order(r: 'requests.models.Response')\
    -> list:
    df = pd.json_normalize(r.json()['data']['tseries'])
    idx_dict = {}
    idx_lst = []
    for vn in varstr_dict:
        idx = df['header.extra.element.id']\
                [df['header.extra.element.id'] == vn].index.to_list()
        idx_dict[vn] = idx
        idx_lst.append(idx)
    return idx_dict, flatten(idx_lst)

flatten = lambda l: [item for sublist in l for item in sublist]

def sort_df(df: 'pandas.core.frame.DataFrame')\
    -> 'pandas.core.frame.DataFrame':
    """
    sort dataframe according to instrument number and rename accordingly
    """
    # get list of aliases
    alst = []
    for vn in varstr_dict:
        alst.append(varstr_dict[vn]['alias'])
    # get list of df element keys and exclude other than str
    elst = [e for e in list(df.keys()) if isinstance(e,str)]
    # make sure that elst includes only strings
    nelst = []
    nelst.append('time')
    for va in alst:
        tmp = [elst[i] for i in range(len(elst)) if va in elst[i]]
        tmp.sort()
        # find doubles
        for n in tmp:
            if n in nelst:
                pass
            else:
                nelst.append(n)
    # reorganize df according to sorted keys and return
    return df[nelst]

def format_df(df: 'pandas.core.frame.DataFrame')\
    -> list:
    """
    format data dataframe
    """
    df = df.rename(columns={ df.columns[0]: '' })
    # quick and dirty formatting
    # - need a smarter and more generic formatter
    dfstr = df.to_string(
        formatters = {
                    #"Hs_0": "{:,.1f}".format,
                    "Hs_0": "{:7.1f}".format,
                    "Hs_1": "{:,.1f}".format,
                    "Hs_2": "{:,.1f}".format,
                    "Hs_3": "{:,.1f}".format,
                    "Hs_4": "{:,.1f}".format,
                    "Hs_5": "{:,.1f}".format,
                    #
                    "Tm02_0": "{:7.1f}".format,
                    "Tm02_1": "{:,.1f}".format,
                    "Tm02_2": "{:,.1f}".format,
                    "Tm02_3": "{:,.1f}".format,
                    "Tm02_4": "{:,.1f}".format,
                    "Tm02_5": "{:,.1f}".format,
                    #
                    "Tp_0": "{:7.1f}".format,
                    "Tp_1": "{:,.1f}".format,
                    "Tp_2": "{:,.1f}".format,
                    "Tp_3": "{:,.1f}".format,
                    "Tp_4": "{:,.1f}".format,
                    "Tp_5": "{:,.1f}".format,
                    #
                    "FF_0": "{:7.1f}".format,
                    "FF_1": "{:,.1f}".format,
                    "FF_2": "{:,.1f}".format,
                    "FF_3": "{:,.1f}".format,
                    "FF_4": "{:,.1f}".format,
                    "FF_5": "{:,.1f}".format,
                    #
                    "DD_0": "{:7.0f}".format,
                    "DD_1": "{:,.0f}".format,
                    "DD_2": "{:,.0f}".format,
                    "DD_3": "{:,.0f}".format,
                    "DD_4": "{:,.0f}".format,
                    "DD_5": "{:,.0f}".format,
                    #
                    "Ta_0": "{:7.1f}".format,
                    "Ta_1": "{:,.1f}".format,
                    "Ta_2": "{:,.1f}".format,
                    "Ta_3": "{:,.1f}".format,
                    "Ta_4": "{:,.1f}".format,
                    "Ta_5": "{:,.1f}".format,
                    #
                    "DDP_0": "{:7.1f}".format,
                    "DDP_1": "{:,.1f}".format,
                    "DDP_2": "{:,.1f}".format,
                    "DDP_3": "{:,.1f}".format,
                    "DDP_4": "{:,.1f}".format,
                    "DDP_5": "{:,.1f}".format,
                    #
                    "Hmax_0": "{:7.1f}".format,
                    "Hmax_1": "{:,.1f}".format,
                    "Hmax_2": "{:,.1f}".format,
                    "Hmax_3": "{:,.1f}".format,
                    "Hmax_4": "{:,.1f}".format,
                    "Hmax_5": "{:,.1f}".format,
                    #
                    "HLAT_0": "{:7.2f}".format,
                    "HLAT_1": "{:,.2f}".format,
                    "HLAT_2": "{:,.2f}".format,
                    "HLAT_3": "{:,.2f}".format,
                    "HLAT_4": "{:,.2f}".format,
                    "HLAT_5": "{:,.2f}".format,
                    #
                    "FG10_0": "{:7.1f}".format,
                    "FG10_1": "{:,.1f}".format,
                    "FG10_2": "{:,.1f}".format,
                    "FG10_3": "{:,.1f}".format,
                    "FG10_4": "{:,.1f}".format,
                    "FG10_5": "{:,.1f}".format,
                    #
                    "FG20_0": "{:7.1f}".format,
                    "FG20_1": "{:,.1f}".format,
                    "FG20_2": "{:,.1f}".format,
                    "FG20_3": "{:,.1f}".format,
                    "FG20_4": "{:,.1f}".format,
                    "FG20_5": "{:,.1f}".format,
                    '': lambda x: "{:%Y-%m-%d %H:%M UTC }".\
                            format(pd.to_datetime(x, unit="ns"))
                    },
        index=False).split('\n')
    return dfstr

def format_info_df(
    df: 'pandas.core.frame.DataFrame',
    fdf: list,
    dinfo: dict,
    attribute: str,
    )\
    -> str:
    """
    format data dataframe of extra info
    """
    fstr = fdf[0]
    klst = list(df.keys())
    klst.remove('time')
    for n,key in enumerate(klst):
        idx = fstr.index(key)
        if n == 0:
            rstr = idx * " " + key
            val = dinfo[attribute][key]
            if np.isnan(val):
                vstr = attribute\
                    + (idx-len(attribute)) * " "\
                    + len(key)* " "
            else:
                template = "{:" + str(len(key)) + ".0f}"
                valstr = template.format(val)
                vstr = attribute\
                    + (idx-len(attribute)) * " "\
                    + valstr
        else:
            rstr += (idx-len(rstr))* " " + key
            val = dinfo[attribute][key]
            if np.isnan(val):
                vstr += (idx-len(vstr))* " " + len(key)* " "
            else:
                #template = "{:" + str(len(key)) + ".1f}"
                template = "{:" + str(len(key)) + ".0f}"
                valstr = template.format(val)
                vstr += (idx-len(vstr))* " " + valstr
    return vstr

def print_formatted(dfstr: list, dfstr_info: str = None):
    """
    print formatted output of retrieved dataframe to screen
    """
    print('')
    print('\n'.join(dfstr[0:1]))
    print('\n'.join(dfstr[1:]))
    print('\n'.join(dfstr[0:1]))
    print('')
    if dfstr_info is not None:
        print(dfstr_info)
    print('')

def print_info(r: 'requests.models.Response', nID: str = None):
    print('\n')
    print('--> ', nID, ' <--')
    dfkeys = pd.json_normalize(\
             r.json()['data']['tseries'][0]['observations']).keys()
    if 'body.lat' in dfkeys:
        # print recent location if moving
        lat = pd.json_normalize(r.json()\
                    ['data']['tseries'][0]['observations'])\
                    ['body.lat'].to_frame()['body.lat'].values[-1]
        lon = pd.json_normalize(r.json()\
                    ['data']['tseries'][0]['observations'])\
                    ['body.lon'].to_frame()['body.lon'].values[-1]
        # type of lat/lon is str
        print( "Location (recent): " + lon + "E " + lat + "N" )
    else:
        # print location if static
        df = pd.json_normalize(r.json()['data']['tseries'])
        lon = float(\
            df['header.extra.station.location']\
            [0][0]\
            ['value']['longitude'])
        lat = float(\
            df['header.extra.station.location']\
            [0][0]\
            ['value']['latitude'])
        print(\
                "Location (i.e. sensor #0): {:.2f}E".format(lon) \
              + " {:.2f}N".format(lat) )

def print_available_locations():
    """
    print available offshore locations
    """
    l = list(range(1, len(insitu_dict.keys())+1))
    dfc = pd.DataFrame(l)
    dfc = dfc.rename(columns={dfc.columns[0]: ''})
    df = pd.DataFrame(insitu_dict.keys())
    df = df.rename(columns={df.columns[0]: 'available locations'})
    dfc = pd.concat([dfc, df.reindex(dfc.index)], axis=1)
    dfstr = dfc.to_string(index=False).split('\n')
    print('----------------------')
    print('\n'.join(dfstr[0:1]))
    print('----------------------')
    print('\n'.join(dfstr[1:]))
    print('----------------------')
    print('Info:')
    print('above shown location aliases can be' \
            ' customized in insitu_locations.yaml')
    print('----------------------')

def dump(df: 'pandas.core.frame.DataFrame', ptf: str, f: str):
    """
    write retrieved data to file
    """
    if f == 'nc':
        # make a new data frame of column headers and number sequentially
        dfcolumns = pd.DataFrame({'name': df.columns})
        dfcolumns['counter'] = dfcolumns.groupby('name').cumcount().apply(lambda n: '_dup' + str(n))

        # remove counter for first case (optional) and combine suffixes
        dfcolumns.loc[dfcolumns.counter=='_dup0', 'counter'] = ''
        df.columns = dfcolumns['name'] + dfcolumns['counter']

        ds = df.to_xarray()
        ds.to_netcdf(ptf)
    elif f == 'p':
        df.to_pickle(ptf)
    elif f == 'csv':
        df.to_csv(ptf)

def runmean(vec, win, mode=None, weights=None, circ=False) -> tuple:
    """
    Computes the running mean with various configurations.

    Args:
        vec (numpy.ndarray | list): array of values to me smoothed
        win (int): window length
        mode (str): string: left, centered, right
        weights (numpy.ndarray | list): weights (same size as win)

    Returns:
        tuple (out (numpy.ndarray), std (numpy.ndarray)):
                array of smoothed values and std deviation
    """
    win = int(win)
    if mode is None:
        mode = 'centered'
    out = np.zeros(len(vec))*np.nan
    std = np.zeros(len(vec))*np.nan
    if mode == 'left':
        length = len(vec)
        start = win-1
        for i in range(start, length):
            out[i] = np.mean(vec[i-win+1:i+1])
            std[i] = np.std(vec[i-win+1:i+1])
            if circ is True:
                out[i] = sp.stats.circmean(vec[i-win+1:i+1],
                                           low=0, high=360)
    elif mode == 'centered':
        length = len(vec)-floor(win/2)
        start = int(floor(win/2))
        for i in range(start, length):
            if win % 2 == 0:
                sys.exit("window length needs to be odd!")
            else:
                sidx = int(i-start)
                eidx = int(i+start+1)
                if weights is not None:
                    out[i] = np.sum(vec[sidx:eidx]*weights)
                    if circ is True:
                        out[i] = sp.stats.circmean(vec[sidx:eidx]*weights,
                                                   low=0, high=360)
                else:
                    out[i] = np.mean(vec[sidx:eidx])
                    if circ is True:
                        out[i] = sp.stats.circmean(vec[sidx:eidx],
                                                   low=0, high=360)
                std[i] = np.std(vec[sidx:eidx])
    elif mode == 'right':
        length = len(vec)
        for i in range(length-win+1):
            out[i] = np.mean(vec[i:i+win])
            std[i] = np.std(vec[i:i+win])
            if circ is True:
                out[i] = sp.stats.circmean(vec[i:i+win], low=0, high=360)
    return out, std

def averager(var, A, win, mode):
    if "Hs" in var:
        A_new = np.sqrt(runmean(A**2, win=win, mode=mode)[0])
    elif ("DD" in var or "DDP" in var):
        A_new = runmean(A, win=win, mode=mode, circ=True)[0]
    else:
        A_new = runmean(A, win=win, mode=mode)[0]
    return A_new
