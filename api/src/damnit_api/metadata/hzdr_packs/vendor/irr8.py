"""
This .py file contains functions:

parse_parameter(line): Parse one parameter line from the IRR8 header and return its name, units, and value.
read_irr8(file_path): Read one IRR8 file and return its parameters and numerical columns as a dictionary.
get_measurement_time(file_path): Extract the measurement date and time from an IRR8 file name.
find_irr8_files(folder): Find and chronologically sort all IRR8 files inside a folder and its subfolders.
read_all_irr8(folder): Read all IRR8 files in chronological order and return their measurements as a dictionary.
add_irr8_to_nexus(nx, entry, measurement, file_name=None,file_path=None):
        Add the IRR8 data, processing information, metadata, fabrication
        details, and original file information to an open NeXus file.

This module does not create the NeXus container. The container is created in the main script and passed to add_irr8_to_nexus()!!
"""

# Read and add the IRR8 diagnostic to an open NeXus file

import math
from pathlib import Path
from datetime import datetime

# Map German month abbreviations from IRR8 file names to month numbers
GERMAN_MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mrz": 3,
    "Apr": 4,
    "Mai": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Okt": 10,
    "Nov": 11,
    "Dez": 12,
}


# Process lines 1-4 in the IIR8 file : example "Integration time [ms]: 35,000"
def parse_parameter(line):
    # Remove spaces at the beginning and end
    line = line.strip()  
    # Separate the parameter description from its value
    left_part, value = line.split(":", maxsplit = 1) 
    # Extract the parameter name and units
    name = left_part.split("[")[0].strip()
    units = left_part.split("[")[1].split("]")[0]
    # "[name]" marks a text value (the spectrometer's serial), not a unit;
    # NDS's reader reads it the same way (W5.3, ruled 2026-10-02).
    if units.strip().lower() == "name":
        units = None
    value = value.replace(",", ".").strip()
    # Convert numerical values to float 
    try:
        value = float(value)
    except ValueError:
        pass

    return name, units, value 


# Read and process one IRR8 file

def read_irr8(file_path):

    with open(file_path, "r", encoding="cp1252") as file:
        lines = file.readlines()
    if len(lines) < 8:
        raise ValueError(f"Incomplete IRR8 header in {file_path}")

    # Create a final dictionary to store all information from one IIR8 file
    measurement = {}
    

    # Read the measurement parameters (lines [1; 4], line 0 is empty)
    for line in lines[1:5]:
        name, units, value = parse_parameter(line)
        measurement[name] = {
            "value": value,
            "units": units
        }
    
    # Read the column names (line 5 in the IIR8 file)
    column_names = lines[5].strip().split(";")
    column_names = [name.strip().lower().replace(" ", "_") for name in column_names]


    # Read the column units (line 6 in the IIR8 file)
    column_units = lines[6].strip().split(";")
    column_units = [unit.strip().replace("[", "").replace("]", "") for unit in column_units]
    if (not all(column_names) or len(set(column_names)) != len(column_names)
            or len(column_units) != len(column_names)):
        raise ValueError(f"Invalid IRR8 columns or units in {file_path}")


    # Create an empty data list for each column 
    for name, unit in zip(column_names, column_units):
        measurement[name] = {
            "data": [],
            "units": unit
        }
    
    # Read the numerical data (lines [8, 102] in the iir8 file)
    for idx, line in enumerate(lines[8:], start = 8):
        line = line.strip()
        # We have empty lines in the file => skip them!
        if not line:
            continue
        # Convert values from lines into float numbers
        values = line.split(";")
        if len(values) != len(column_names):
            raise ValueError(f"{Path(file_path).name} line {idx + 1}: expected "
                             f"{len(column_names)} columns, got {len(values)}")
        try:
            values = [float(value.strip().replace(",", ".")) for value in values]
        except ValueError as error:
            raise ValueError(f"{Path(file_path).name} line {idx + 1}: invalid number") from error
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"{Path(file_path).name} line {idx + 1}: non-finite number")
        # Fill out the dictionary with actual numbers from the IIR8 file
        for name, value in zip(column_names, values):
            measurement[name]["data"].append(value)

    if not measurement[column_names[0]]["data"]:
        raise ValueError(f"No spectral samples in {file_path}")
    return measurement




def get_measurement_time(file_path):
    # Extract the measurement timestamp from an IRR8 file name.
    file_parts = Path(file_path).name.split("_")

    date_part = file_parts[1]
    time_part = file_parts[2]
    # Convert the German date into numerical date components (day, month, year)
    day = int(date_part[:2])
    month = GERMAN_MONTHS[date_part[2:5]]
    year = 2000 + int(date_part[5:7])
    # Extract the time components (hour, minute, second)
    hour = int(time_part[:2])
    minute = int(time_part[2:4])
    second = int(time_part[4:6])

    return datetime(year, month, day, hour, minute, second)


def find_irr8_files(folder):
    # Find and chronologically sort IRR8 files in all subfolders.
    folder = Path(folder)
    # Return an empty list if the folder does not exist.
    if not folder.exists():
        print(f"IRR8 folder not found: {folder}")
        return []

    return sorted(folder.rglob("*.Irr8.txt"), key = get_measurement_time)


def read_all_irr8(folder):
    # Read all chronologically sorted IRR8 files in a folder.
    all_measurements = {}

    for file_path in find_irr8_files(folder):
        all_measurements[file_path.name] = read_irr8(file_path)

    return all_measurements

def add_irr8_to_nexus(nx, entry, measurement, file_name = None, file_path = None):
    from nexusformat.nexus import (
        NXcollection, NXdetector, NXfabrication, NXfield, NXnote, NXparameters, NXprocess,
    )

    # Create the NXdetector base class for the spectrometer.
    diagnostic_path = f"{entry}/515 Reflected Light Spectrometer/Spectrometer"
    nx[diagnostic_path] = NXdetector()

    nx[f"{diagnostic_path}/raw_data"] = NXcollection()
    nx[f"{diagnostic_path}/raw_data/wavelength"] = NXfield(measurement["wave"]["data"], units=measurement["wave"]["units"])
    nx[f"{diagnostic_path}/raw_data/sample"] = NXfield(measurement["sample"]["data"], units=measurement["sample"]["units"])
    nx[f"{diagnostic_path}/raw_data/dark"] = NXfield(measurement["dark"]["data"], units=measurement["dark"]["units"])
    nx[f"{diagnostic_path}/raw_data/reference"] = NXfield(measurement["reference"]["data"], units=measurement["reference"]["units"])
    nx[f"{diagnostic_path}/raw_data/absolute_irradiance"] = NXfield(measurement["absolute_irradiance"]["data"], units=measurement["absolute_irradiance"]["units"])
    nx[f"{diagnostic_path}/raw_data/photon_counts"] = NXfield(measurement["photon_counts"]["data"], units=measurement["photon_counts"]["units"])

    # Store information about data processing. Create a NXprocess base class
    process_path = f"{diagnostic_path}/processing"
    nx[process_path] = NXprocess()
    nx[f"{process_path}/date"] = NXfield(datetime.now().astimezone().isoformat(timespec="seconds"))
    nx[f"{process_path}/parameters"] = NXparameters()
    nx[f"{process_path}/parameters/smoothing_width"] = NXfield(measurement["Smoothing Nr."]["value"], units=measurement["Smoothing Nr."]["units"])
    # Store the spectrometer acquisition metadata in the NXcollection() base class
    nx[f"{diagnostic_path}/metadata"] = NXcollection()
    nx[f"{diagnostic_path}/metadata/name"] = NXfield(measurement["Data measured with spectrometer"]["value"])
    nx[f"{diagnostic_path}/metadata/integration_time"] = NXfield(measurement["Integration time"]["value"], units=measurement["Integration time"]["units"])
    nx[f"{diagnostic_path}/metadata/averaging"] = NXfield(measurement["Averaging Nr."]["value"], units=measurement["Averaging Nr."]["units"])

    file_info_path = f"{diagnostic_path}/file_metadata"
    nx[file_info_path] = NXnote()
    # Remove the date field automatically created by NXnote.
    if "date" in nx[file_info_path]:
        del nx[f"{file_info_path}/date"]
    
    nx[f"{file_info_path}/PC_name"] = NXfield("")

    if file_name is not None:
        nx[f"{file_info_path}/file_name"] = NXfield(str(file_name))
        measurement_date = get_measurement_time(file_name)
        nx[f"{file_info_path}/file_creation_date"] = NXfield(measurement_date.isoformat())

        serial_number = Path(file_name).name.split("_", maxsplit = 1)[0]
        fabrication_path = f"{diagnostic_path}/fabrication"
        nx[fabrication_path] = NXfabrication()
        nx[f"{fabrication_path}/serial_number"] = NXfield(serial_number)

    if file_path is not None:
        nx[f"{file_info_path}/file_path"] = NXfield(str(Path(file_path).resolve()))
    # Link the absolute irradiance and wavelength to the instrument NXdata group
    data = nx[f"{entry}/515 Reflected Light Spectrometer/data"]
    data.makelink(nx[f"{diagnostic_path}/raw_data/absolute_irradiance"])
    data.makelink(nx[f"{diagnostic_path}/raw_data/wavelength"])
    # Define absolute irradiance as the signal and wavelength as its axis
    data.nxsignal = data["absolute_irradiance"]
    data.nxaxes = [data["wavelength"]]
