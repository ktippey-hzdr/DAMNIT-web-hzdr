"""
This .py file contains functions:

find_image_files(folder): Find and sort all original PNG images inside a folder and its subfolders
get_image_time(png_file): Extract the measurement date and time from the PNG file name
get_csv_file(png_file) Get .csv's file name
read_image(png_file): Read a PNG image without changing its original format
read_csv_metadata_file(csv_file):  Read the corresponding CSV file and return its metadata as a dictionary
get_shot_number(png_file): Extract the laser shot number from the PNG file name
add_image_csv_to_nexus(nx, entry, image, metadata): Add the image, camera metadata, fabrication information, and original
        file information to an already open NeXus file. This module does not create the NeXus container. The container is created
        in the main script and passed to add_image_csv_to_nexus().

"""

# Read and add the image/CSV diagnostic to an open NeXus file.

import csv
from pathlib import Path
from datetime import datetime

def read_image(png_file):
    import cv2

    # Read the image without changing its original format
    image = cv2.imread(str(png_file), cv2.IMREAD_UNCHANGED) # Reads the image with transparency

    # Return None if the image cannot be read
    if image is None:
        print(f"Skipping unreadable image: {png_file}")
        return None

    return image


def read_csv_metadata_file(csv_file):
    metadata = {}
    with open(csv_file, encoding="cp1252", newline="") as source:
        # The first twelve lines are the camera report preamble.
        for _ in range(12):
            next(source, None)
        for row in csv.reader(source, delimiter=";"):
            # Empty values occupy a column. Removing them shifts later pairs,
            # e.g. Comment;;Name;Camera would turn Name into Comment's value.
            for i in range(0, len(row), 2):
                key = row[i].strip()
                value = row[i + 1].strip() if i + 1 < len(row) else ""
                if key:
                    if key in metadata and metadata[key] != value:
                        raise ValueError(f"Conflicting camera metadata {key!r} in {csv_file}")
                    metadata[key] = value
    return metadata

def get_image_time(png_file):
    # Extract the measurement date and time from the PNG file name
    parts = Path(png_file).stem.split("_")
    return datetime.strptime(f"{parts[1]}_{parts[2]}", "%Y-%m-%d_%Hh-%Mm-%Ss")

def get_shot_number(png_file):
    # Extract the laser shot number from the image file name: example "set1_2025-12-01_15h-59m-04s_2_original.png"
    parts = png_file.stem.split("_")
    shot_number = int(parts[3])
    return shot_number


def find_image_files(folder):
    # Find and sort all original PNG images in a folder and its subfolders.
    return sorted(Path(folder).rglob("*_original.png"))


def get_csv_file(png_file):
    #Return the CSV metadata path corresponding to an image.
    png_file = Path(png_file)
    return png_file.with_name(png_file.name.replace("_original.png", ".csv"))


def add_image_csv_to_nexus(nx, entry, image, metadata, file_name = None, img_path = None, csv_file_path = None):
    from nexusformat.nexus import NXcollection, NXdata, NXdetector, NXfabrication, NXfield, NXnote

    diagnostic_path = f"{entry}/515 Reflected Light Spectrometer/Fiber_Entrance_Camera"
    nx[diagnostic_path] = NXdetector()

    acquisition_path = f"{diagnostic_path}/raw_data"
    nx[acquisition_path] = NXcollection()

    nx[f"{acquisition_path}/image"] = NXfield(image, units = 'counts', compression = 'gzip')
    # Create an NXdata view containing a link to the original image.
    nx[f"{diagnostic_path}/data"] = NXdata()
    nx[f"{diagnostic_path}/data"].makelink(nx[f"{acquisition_path}/image"])
    nx[f"{diagnostic_path}/data"].nxsignal = nx[f"{diagnostic_path}/data/image"]
    nx[f"{acquisition_path}/name"] = NXfield(metadata.get("Label", ""))
    nx[f"{acquisition_path}/sequence_number"] = NXfield(metadata.get("Image No.", ""))
    # Store camera fabrication information.
    nx[f"{diagnostic_path}/fabrication"] = NXfabrication()
    nx[f"{diagnostic_path}/fabrication/model"] = NXfield(metadata.get("Name", ""))
    # Store camera acquisition settings from the CSV file.
    nx[f"{diagnostic_path}/metadata"] = NXcollection()
    nx[f"{diagnostic_path}/metadata/black_level_offset"] = NXfield(metadata.get("Black Level Offset", ""))
    nx[f"{diagnostic_path}/metadata/chip_size_x"] = NXfield(metadata.get("Chip Size X", ""))
    nx[f"{diagnostic_path}/metadata/chip_size_y"] = NXfield(metadata.get("Chip Size Y", ""))
    nx[f"{diagnostic_path}/metadata/exposure"] = NXfield(metadata.get("Exposure", ""))
    nx[f"{diagnostic_path}/metadata/gain"] = NXfield(metadata.get("Gain", ""))
    nx[f"{diagnostic_path}/metadata/gamma"] = NXfield(metadata.get("Gamma", ""))

    file_info_path = f"{diagnostic_path}/file_metadata"
    nx[file_info_path] = NXnote()
     # Remove the date field automatically created by NXnote
    if "date" in nx[file_info_path]:
        del nx[f"{file_info_path}/date"]

    nx[f"{file_info_path}/description"] = NXfield(metadata.get("Comment", ""))
    nx[f"{file_info_path}/PC_name"] = NXfield("")

    if file_name is not None:
        nx[f"{file_info_path}/file_name"] = NXfield(str(file_name))
    if img_path is not None:
        measurement_date = get_image_time(img_path)
        nx[f"{file_info_path}/file_creation_date"] = NXfield(measurement_date.isoformat())
        nx[f"{file_info_path}/file_path"] = NXfield(str(Path(img_path).resolve()))
    if csv_file_path is not None:
        nx[f"{file_info_path}/metadata_file_path"] = NXfield(str(Path(csv_file_path).resolve()))
   
    
