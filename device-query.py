#!/usr/bin/env python
# """Module docstring."""

# Imports
from netmiko import ConnectHandler
import sys
import csv
import datetime
import os
import time
import re


# Module 'Global' variables, custom absolute paths
DEVICE_FILE_PATH = "C:/Temp/_CiscoDevNet/devices.csv"  # file should contain a list of devices in format: ip,username,password,device_type
BACKUP_DIR_PATH = "C:/Temp/_CiscoDevNet/backup"  # complete path to backup directory


def get_devices_from_file(device_file):
    # vars
    device_list = list()

    # reading a CSV file with ',' as a delimiter
    with open(device_file, 'r') as f:
        reader = csv.DictReader(f, delimiter=',')
        # every device represented by single row which is a dictionary object with keys equal to column names.
        for row in reader:
            dup = False
            # check hostname duplicates and skip it
            for dev in device_list:
                if dev['hostname'] == row['hostname']:
                    print("Duplicated device '{}' - skipped".format(row['hostname']))
                    dup = True
            if not dup:
                print("Add unique device '{}' to device list".format(row['hostname']))
                device_list.append(row)

    return device_list


def get_current_date_and_time():
    now = datetime.datetime.now()

    # Format: yyyy_mm_dd-hh_mm_ss
    timestamp = now.strftime("%Y_%m_%d-%H_%M_%S")

    return timestamp


def connect_to_device(device):
    # Since there is a 'hostname' key, this dictionary can't be used as is
    connection = ConnectHandler(
        host=device['ip'],
        username=device['username'],
        password=device['password'],
        device_type=device['device_type'],
        secret=device['secret']
        # ,session_log="c:/temp/_CiscoDevNet/log.txt"
    )

    return connection


def disconnect_from_device(connection, hostname):
    connection.disconnect()


def get_backup_file_path(hostname, timestamp):
    # checking if backup directory exists for the device, creating it if not present
    if not os.path.exists(os.path.join(BACKUP_DIR_PATH, hostname)):
        os.makedirs(os.path.join(BACKUP_DIR_PATH, hostname))

    # Merging a string to form a full backup file name
    backup_file_path = os.path.join(BACKUP_DIR_PATH, hostname, "{}-{}.txt".format(hostname, timestamp))

    return backup_file_path


def create_backup(connection, backup_file_path, hostname):
    try:
        #        print("Sending command 'sh run' to device")
        output = connection.send_command("sh run")

        # creating a backup file and writing command output to it
        with open(backup_file_path, 'w') as file:
            file.write(output)
        #        print("Backup of " + hostname + " is complete!")

        return True
    except Error:
        print("Error! Unable to backup device '{}'".format(hostname))
        return False


def get_device_inv_info(connection):
    try:
        #        print("Sending command 'sh inv' to device")
        output = connection.send_command("sh inv")
        # parse output and get only the first PID assuming it's chassis PID
        output = output[output.find("PID:") + 4:]
        output = output[0:output.find(",")]
        return output.strip()
    except Error:
        print("Error! Unable to get inventory info from '{}'".format(hostname))
        return "DEVICE INVENTORY FAILED"


def get_device_ver_info(connection):
    # vars
    pe_npe = "PE "

    try:
        #        print("Sending command 'sh ver' to device")
        output = connection.send_command("sh ver")
        # get only string that contains "System image file is" and sanitize it
        idx = output.find("System image file is") + len("System image file is")
        output = re.sub(r"\.bin$", "", output[idx:output.find("\n", idx)].strip().replace('"', ''))
        # take only substring after ':' character if any
        output = output[output.find(":") + 1:]
        # split path by '/' (if any) and take only filename
        path_items = output.split("/")
        output = path_items[len(path_items) - 1]
        # check if NPE
        if output.lower().find("npe") != -1:
            pe_npe = "NPE"
        return output + " | " + pe_npe
    except Error:
        print("Error! Unable to get version info from '{}'".format(hostname))
        return "DEVICE VERSION FAILED"


def get_device_cdp_info(connection):
    # vars
    cdp_run = "OFF"
    cdp_neighbours = 0

    try:
        #        print("Sending command 'sh cdp' to device")
        output = connection.send_command("sh cdp")
        if output.find("CDP is not enabled") < 0:
            cdp_run = "ON"
    except Error:
        print("Error! Unable to get CDP info from '{}'".format(hostname))
        return "DEVICE CDP RUN FAILED"

    # if CDP is running, count number of neighbours
    if cdp_run == "ON":
        try:
            #            print("Sending command 'sh cdp nei' to device")
            output = connection.send_command('sh cdp nei')
            output = output[output.find("Device ID"):len(output)]

            # initially decrease neighbor counter as header line is included as well
            cdp_neighbours -= 1
            for str1 in output.splitlines():
                # add neighbour counter if this string is not line transition for previous one
                if not str1.startswith(" "):
                    cdp_neighbours += 1
        except Error:
            print("Error! Unable to get CDP neigbours info from '{}'".format(hostname))
            return "DEVICE CDP NEIGHBOURS FAILED"

    return "CDP is " + cdp_run + ((", " + str(cdp_neighbours) + " peers") if cdp_run == "ON" else "")


def set_timezone_gmt0(connection):
    try:
        #        print("Sending config command 'clock timezone GMT 0 0' to device")
        connection.send_config_set("clock timezone GMT 0 0")
        return True
    except Error:
        print("Error! Unable to set timezone GMT+0 on '{}'".format(hostname))
        return False


def getset_device_ntp_info(connection, ntpserver):
    # vars
    ntp_status = ""

    try:
        output = connection.send_command("ping " + ntpserver)
        if output.find("Success rate is 0") != -1:
            ntp_status = "NTP server not reachable, "
        else:
            # set reachable NTP server
            connection.send_config_set("ntp server " + ntpserver)

        # check clock synchronization with currently set NTP if any
        #        print("Sending command 'sh ntp status' to device")
        output = connection.send_command("sh ntp status | i Clock is")
        if output.find("Clock is unsynchronized") != -1:
            return ntp_status + "Clock is not sync"
        return ntp_status + "Clock is Sync"
    except Error:
        print("Error! Unable to get NTP info from '{}'".format(hostname))
        return "DEVICE NTP FAILED"


def process_target(device):
    timestamp = get_current_date_and_time()

    # connect to device and do some manipulations
    connection = connect_to_device(device)
    connection.enable()
    # Netmiko sends 'term len 0'/'term width 511' before enable mode, not everywhere it works,
    # so repeat the command ourselves manually once again being in enable mode already
    connection.send_command("terminal len 0")
    time.sleep(1)
    connection.send_command("terminal width 511")
    time.sleep(1)

    # get backup
    backup_file_path = get_backup_file_path(device['hostname'], timestamp)
    create_backup(connection, backup_file_path, device['hostname'])

    # get device model info
    device_inv = get_device_inv_info(connection)

    # get device IOS version info
    device_ver = get_device_ver_info(connection)

    # get CDP info
    device_cdp = get_device_cdp_info(connection)

    # set timezone GMT+0
    # it works, but commented not to break production
    #    set_timezone_gmt0(connection)

    # get/set NTP info
    device_ntp = getset_device_ntp_info(connection, device['ntp'])

    # print out final result
    print(device['hostname']+" | "+device_inv+" | "+device_ver+" | "+device_cdp+" | "+device_ntp)

    # disconnect from device at the end
    disconnect_from_device(connection, device['hostname'])


def main(*args):
    # This is a main function

    # getting a device list from the file in a python format
    device_list = get_devices_from_file(DEVICE_FILE_PATH)
    for dev in device_list:
        process_target(dev)


if __name__ == '__main__':
    # checking if we run independently
    _, *script_args = sys.argv

    # the execution starts here
    main(*script_args)
