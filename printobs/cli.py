#!/usr/bin/env python3
import argparse
from argparse import RawTextHelpFormatter
from datetime import datetime, timedelta
import time
import os
from .utils import parse_date, call_frost_api
from .utils import get_frost_df, print_formatted
from .utils import print_available_locations
from .utils import format_df, format_info_df
from .utils import sort_df
from .utils import dump
from .utils import print_info

def main():
    parser = argparse.ArgumentParser(description="""
    print offshore insitu observations

    Usage:

    For a list of available stations:
    printobs

    Qyery a specific station:
    printobs -s draugen

    Extend query back in time (here e.g. 24h):
    printobs -s draugen -d 24

    Adding start and end date to query:
    printobs -s draugen -sd 20220401 -ed 20220404
    printobs -s draugen -sd 2022-04-01-12 -ed 2022.04.04-06:15

    Output hourly averages of Hs related variables:
    printobs -s draugen -sd 20220401 -ed 20220404 -avVar Hs
    This is equivalent to the default:
    printobs -s draugen -sd 20220401 -ed 20220404 -avVar Hs -avMode left -avWin 6

    """, formatter_class=RawTextHelpFormatter)
    parser.add_argument("-sd", metavar='startdate',
                        help="start date of time period to be downloaded")
    parser.add_argument("-ed", metavar='enddate',
                        help="end date of time period to be downloaded")
    parser.add_argument("-d", type=int, metavar='delta',
                        help="substracted hours from now (in lieu of sd & ed)")
    parser.add_argument("-s", metavar='station', help="station")
    parser.add_argument("-i", metavar='instrument', help="instrument")
    parser.add_argument("-v", metavar='version', help="FROST API version")
    parser.add_argument("-w", metavar='write',
            help="choose write format, possible writers are:\n\
            nc - netcdf\n\
            p - pickle\n\
            csv - csv")
    parser.add_argument("-p", metavar='path', help="path to the target file")
    parser.add_argument("-avVar", metavar='averageVar', help="average of chosen variable")
    parser.add_argument("-avMode", metavar='averageMode', help="mode for averaging: left, centered, right")
    parser.add_argument("-avWin", metavar='averageWindow', help="window for averaging (nr of obs)")

    args = parser.parse_args()
    dargs = vars(args)

# remove all None entries
    dargs = {k: v for k, v in dargs.items() if v is not None}

    ed = parse_date(dargs.get('ed', datetime.now() + timedelta(hours=3)))
    sd = parse_date(dargs.get('sd', datetime.now() - timedelta(hours=12)))

    if args.d is not None:
        sd = parse_date(ed) - timedelta(hours=args.d) - timedelta(hours=3)

    s = dargs.get('s')
    i = None
    v = dargs.get('v', 'v1')
    w = dargs.get('w')
    p = dargs.get('p')
    avVar = dargs.get('avVar', None)
    avMode = dargs.get('avMode', 'left')
    avWin = dargs.get('avWin', 6)

# -------------------------------------------------------------------- #
    if s is None:
        # print available locations
        print_available_locations()
    else:
        t1 = time.time()
        # api call
        r = call_frost_api(sd, ed, s, v)
        #print(r.url)
        t2 = time.time()
        print('time used for api call:', f'{t2-t1:.2f}', 'seconds')
        # get additional info
        if v == 'v1':
            df, dinfo = get_frost_df(r, v)
        else:
            df = get_frost_df(r, v)
        # info_lst = list(dinfo.keys())
        # reorganize df
        df = sort_df(df)
        # format data for output
        fdf = format_df(df)
        # format info df
        fdf_info = None
        if w is None:
            print_formatted(fdf, fdf_info)
            if v == 'v1':
                #print(format_info_df(df,fdf,dinfo,'sensor'))
                print(format_info_df(df, fdf, dinfo, 'level'))
                print(format_info_df(df, fdf, dinfo, 'parameterid'))
            # print to screen
            if v == 'v1':
                print_info(r, s)
            print('')
            t3 = time.time()
            print('time used:', f'{t3-t1:.2f}', 'seconds')
        if avVar is not None:
            from .utils import averager
            import pandas as pd
            l = list(df.keys())
            varlst = [s for s in l if avVar in s]
            df2 = df[['time']+varlst]
            for var in varlst:
                varmean = averager(var, df2[var].values, avWin, avMode)
                with pd.option_context('mode.chained_assignment', None):
                    df2[var] = varmean
            fdf2 = format_df(df2)
            print_formatted(fdf2, None)
    if w is not None:
        dump(df, p, w)
