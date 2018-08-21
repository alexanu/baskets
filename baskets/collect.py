"""Update the holdings database with missing or the newest files.
"""
__author__ = 'Martin Blais <blais@furius.ca>'
__license__ = "GNU GPLv2"

from os import path
from pprint import pprint
from typing import Dict
import argparse
import collections
import contextlib
import csv
import datetime
import logging
import os
import re
import shutil
import time

import numpy
import pandas
import requests
from selenium import webdriver
#from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome import options

from baskets import table
from baskets.table import Table
from baskets import beansupport
from baskets import utils
from baskets import driverlib
from baskets import database
from baskets import issuers
from baskets import graph


def normalize_holdings_table(table: Table) -> Table:
    """The assets don't actually sum to 100%, normalize them."""
    total = sum([row.fraction for row in table])
    if not (0.98 < total < 1.02):
        logging.error("Total weight seems invalid: {}".format(total))
    scale = 1. / total
    return table.map('fraction', lambda f: f*scale)


ASSTYPES = {'Equity', 'FixedIncome', 'ShortTerm'}
IDCOLUMNS = ['name', 'ticker', 'sedol', 'isin', 'cusip']
COLUMNS = ['etf', 'account', 'fraction', 'asstype'] + IDCOLUMNS


def check_holdings(holdings: Table):
    """Check that the holdings Table has the required columns."""
    actual = set(holdings.columns)

    allowed = {'asstype', 'fraction'} | set(IDCOLUMNS)
    other = actual - allowed
    assert not other, "Extra columns found: {}".format(other)

    required = {'asstype', 'fraction'}
    assert required.issubset(actual), (
        "Required columns missing: {}".format(required - actual))

    assert set(IDCOLUMNS) & actual, "No ids columns found: {}".format(actual)

    assert all(cls in ASSTYPES for cls in holdings.values('asstype'))


def add_missing_columns(tbl: Table) -> Table:
    for column in IDCOLUMNS:
        if column not in tbl.columns:
            tbl = tbl.create(column, lambda _: '')
    return tbl


def main():
    logging.basicConfig(level=logging.INFO, format='%(levelname)-8s: %(message)s')
    parser = argparse.ArgumentParser(description=__doc__.strip())

    parser.add_argument('assets_csv',
                        help=('A CSV file which contains the tickers of assets and '
                              'number of units'))

    parser.add_argument('-l', '--ignore-shorts', action='store_true',
                        help="Ignore short positions")
    parser.add_argument('-o', '--ignore-options', action='store_true',
                        help="Ignore options positions")

    parser.add_argument('--dbdir', default=database.DEFAULT_DIR,
                        help="Database directory to write all the downloaded files.")

    parser.add_argument('-F', '--full-table', action='store',
                        help="Path to write the full table to.")

    parser.add_argument('-A', '--agg-table', action='store',
                        help="Path to write the full table to.")

    args = parser.parse_args()
    db = database.Database(args.dbdir)

    # Load up the list of assets from the exported Beancount file.
    assets = beansupport.read_exported_assets(args.assets_csv, args.ignore_options)
    assets.checkall(['ticker', 'account', 'issuer', 'price', 'number'])

    assets = assets.order(lambda row: (row.issuer, row.ticker))

    if 0:
        print()
        print(assets)
        print()

    # Fetch baskets for each of those.
    tickermap = collections.defaultdict(list)
    alltables = []
    for row in assets:
        #if row.issuer != 'iShares': continue ## FIXME: remove

        if row.number < 0 and args.ignore_shorts:
            continue

        if not row.issuer:
            holdings = Table(['fraction', 'asstype', 'ticker'],
                             [str, str, str],
                             [[1.0, 'Equity', row.ticker]])
        else:
            try:
                downloader = issuers.MODULES[row.issuer]
            except KeyError:
                logging.error("Missing issuer %s", row.issuer)
                continue

            filename = database.getlatest(db, row.ticker)
            if filename is None:
                logging.error("Missing file for %s", row.ticker)
                continue
            logging.info("Parsing file '%s' with '%s'", filename, row.issuer)

            if not hasattr(downloader, 'parse'):
                logging.error("Parser for %s is not implemented", row.ticker)
                continue

            # Parse the file.
            holdings = downloader.parse(filename)
            check_holdings(holdings)

        # Add parent ETF and fixup columns.
        holdings = add_missing_columns(holdings)
        holdings = holdings.create('etf', lambda _: row.ticker)
        holdings = holdings.create('account', lambda _: row.account)
        holdings = holdings.select(COLUMNS)

        # Convert fraction to dollar amount.
        dollar_amount = row.number * row.price
        holdings = (holdings
                    .create('amount', lambda row: row.fraction * dollar_amount)
                    .delete(['fraction']))

        alltables.append(holdings)

    # Write out the full table.
    fulltable = table.concat(*alltables)
    logging.info("Total amount from full holdings table: {:.2f}".format(
        numpy.sum(fulltable.array('amount'))))
    if args.full_table:
        with open(args.full_table, 'w') as outfile:
            table.write_csv(fulltable, outfile)

    # Aggregate the holdings.
    aggtable = graph.group(fulltable)
    if args.agg_table:
        with open(args.agg_table, 'w') as outfile:
            table.write_csv(aggtable, outfile)

    # Cull out the tail of holdings for printing.
    tail = 0.98
    amount = aggtable.array('amount')
    total_amount = numpy.sum(amount)
    logging.info('Total: {:.2f}'.format(total_amount))
    cum_amount = numpy.cumsum(amount)
    headsize = len(amount[cum_amount < total_amount * tail])
    print(aggtable.head(headsize))


if __name__ == '__main__':
    main()
