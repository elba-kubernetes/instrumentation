"""
Script to parse rAdvisor container stat logs
"""

__author__ = "Joseph Azevedo"
__version__ = "1.0"

import numpy as np
import pandas as pd
from dateutil import parser
from collections import OrderedDict
from more_itertools import peekable
import glob, os, json, csv, argparse
from csv import Error
import yaml
from yaml import load, dump, Loader


DESCRIPTION = "Script to parse rAdvisor container stat logs"

def in_t(val):
    if val is not None and len(val) > 0:
        return int(val)
    return 0

class DataClass:
    def __getattr__(self, attr):
        try:
            return getattr(self, f"_{attr}")
        except AttributeError:
            pass


class BlkioEntry(DataClass):
    """
    Blkio Linux stats from rAdvisor log entries
    """

    def __init__(self, raw):
        majmin, op, value = raw.split(" ")
        major, minor = majmin.split(":")
        self._major = in_t(major)
        self._minor = in_t(minor)
        self._value = in_t(value)
        self._op = op
        #print()


class BlkioEntry(DataClass):
    """
    Blkio Linux stats from rAdvisor log entries
    """

    def __init__(self, row):
        self._total = in_t(row["cpu.usage.total"])
        self._system_usage = in_t(row["cpu.usage.system"])
        self._user_usage = in_t(row["cpu.usage.user"])
        self._percpu = [in_t(cpu.strip()) for cpu in row["cpu.usage.percpu"].split(' ')]
        self._system_stat = in_t(row["cpu.stat.system"])
        self._user_stat = in_t(row["cpu.stat.user"])
        self._throttling_periods = in_t(row["cpu.throttling.periods"])
        self._throttled_periods = in_t(row["cpu.throttling.throttled.count"])
        self._throttled_time = in_t(row["cpu.throttling.throttled.time"])


class Memory(DataClass):
    """
    Memory Linux stats from rAdvisor log entries
    """

    def __init__(self, row):
        self._usage_current = in_t(row["memory.usage.current"])
        self._max_usage = in_t(row["memory.usage.max"])
        self._hard_limit = in_t(row["memory.limit.hard"])
        self._soft_limit = in_t(row["memory.limit.soft"])
        self._failcnt = in_t(row["memory.failcnt"])
        self._mem_limit = in_t(row["memory.hierarchical_limit.memory"])
        self._swap_limit = in_t(row["memory.hierarchical_limit.memoryswap"])
        self._cache = in_t(row["memory.cache"])
        self._rss_all = in_t(row["memory.rss.all"])
        self._rss_huge = in_t(row["memory.rss.huge"])
        self._mapped = in_t(row["memory.mapped"])
        self._swap = in_t(row["memory.swap"])
        self._paged_in = in_t(row["memory.paged.in"])
        self._paged_out = in_t(row["memory.paged.out"])
        self._fault_total = in_t(row["memory.fault.total"])
        self._fault_major = in_t(row["memory.fault.major"])
        self._anon_inactive = in_t(row["memory.anon.inactive"])
        self._anon_active = in_t(row["memory.anon.active"])
        self._file_inactive = in_t(row["memory.file.inactive"])
        self._file_active = in_t(row["memory.file.active"])
        self._unevictable = in_t(row["memory.unevictable"])


class Blkio(DataClass):
    """
    Blkio Linux stats from rAdvisor log entries
    """

    def __init__(self, row):
        self._service_bytes = parse_blkio(row["blkio.service.bytes"])
        self._serviced = parse_blkio(row["blkio.service.ios"])
        self._service_time = parse_blkio(row["blkio.service.time"])
        self.queued = parse_blkio(row["blkio.queued"])
        self._wait_time = parse_blkio(row["blkio.wait"])
        self._merged = parse_blkio(row["blkio.merged"])
        self._time = parse_blkio(row["blkio.time"])
        self._sectors = parse_blkio(row["blkio.sectors"])


class PidsStats(DataClass):
    """
    Pids Linux stats from rAdvisor log entries
    """

    def __init__(self, row):
        self._current = in_t(row["pids.current"])
        self._max= 0 if row["pids.max"] == "max" else in_t(row["pids.max"])


class LogEntry(DataClass):
    """
    A rAdvisor log entry, generated by the tats collector in CSV mode
    """

    def __init__(self, row, preread=None, entries=None):
        """
        Initializes and parses a LogEntry
        """

        self._read = in_t(row["read"])
        self._cpu = CpuUsage(row)
        self._memory = Memory(row)
        self._pids = PidsStats(row)
        self._blkio = Blkio(row)

        # Try to resolve pre-read statistics
        self._pre = None
        if entries is not None and preread is not None:
            if preread in entries:
                pre_entry = entries[preread]
                if pre_entry is not None:
                    self._pre = pre_entry


def parse_blkio(raw):
    """
    Parses a Blkio entry array
    """

    split = [entry.strip() for entry in raw.split(",")]
    return [BlkioEntry(entry) for entry in split if len(entry.split(" ")) == 3 and "Total" not in entry]


def main(root=None):
    """
    Parses and processes all files in the directory
    """

    resolved_root = os.path.abspath(root) if root is not None else os.getcwd()
    files = get_all_files(resolved_root, "log")
    data = parse_all(files)
    for (_, tup) in data.items():
        entries, metadata = tup
        analyze_timestamps(entries)


def parse_all(files):   
    """
    Parses all of the given CSV filenames
    """

    parsed = {}
    for file in files:
        relative = os.path.relpath(file)
        print(f"Parsing log data from {relative}... ", end="", flush=True)
        log_data = load_file(file)
        print("done")
        parsed[file] = log_data
    return parsed


def analyze_timestamps(entries):
    """
    Analyzes timestamps to verify correct collection interval
    """

    deltas = get_ts_deltas(entries)
    deltas_df = pd.DataFrame({'Read deltas (ms)': deltas})
    print(deltas_df.describe(include='all'))


def get_ts_deltas(entries):
    """
    Gets timestamp deltas
    """

    return find_deltas([float(timestamp) / 1E6 for timestamp in entries])


def remove_outliers(arr, z=1.5):
    """
    Removes outliers (classified as outside [q25 - 1.5*iqr, q75 + 1.5*iqr])
    """

    q25, q75 = np.percentile(arr, 25), np.percentile(arr, 75)
    iqr = q75 - q25
    limit = z * iqr
    return [a for a in arr if a >= q25 - limit and a <= q75 + limit]


def find_deltas(arr):
    """
    Creates a new array that is the differences between consecutive elements in
    a numeric series. The new array is len(arr) - 1
    """

    return [j-i for i, j in zip(arr[:-1], arr[1:])]


def load_file(filename):
    """
    Loads an output file from rAdvisor into an ordered dictionary
    of read timestamp (int) -> LogEntry in the order of logging
    """

    entries = OrderedDict()
    metadata = None

    with open(filename, "r") as csvfile:
        yaml_lines = []
        # Skip the first yaml delimeter
        next(csvfile)

        # Load all lines until the end of the yaml section
        file_iter = peekable(csvfile)
        while not file_iter.peek().startswith("---"):
            yaml_lines.append(next(file_iter))
        # Skip the second yaml delimeter
        next(file_iter)

        # Load YAML to dictionary
        yaml_str = "\n".join(yaml_lines)
        yaml_loader = Loader(yaml_str)
        metadata = yaml_loader.get_data()

        csv_reader = csv.DictReader(file_iter) 

        # skip header row
        next(csv_reader)

        preread = None
        try:
            for row in csv_reader:
                entry = LogEntry(row, entries=entries, preread=preread)
                entries[entry.read] = entry
                preread = entry.read
        except Error as e:
            print(e)
            print("An error ocurred. continuing...\n")

    return (entries, metadata)


def get_all_files(dir, ext):
    """
    Gets all files in the directory with the given extension
    """
    extension_suffix = f".{ext}"
    files = []
    for file in os.listdir(dir):
        if file.endswith(extension_suffix):
            files.append(os.path.join(dir, file))
    return files



def bootstrap():
    """
    Runs CLI parsing/execution
    """

    # Argument definitions
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--root", "-r", metavar="path",
                        help="the path to find log files in (defaults to current directory)")

    # Parse arguments
    parsed_args = parser.parse_args()
    main(root=parsed_args.root)



if __name__ == "__main__":
    bootstrap()