import os
from os import path


def abslistdir(directory):
    """Listdir() with absolute values."""
    filenames = []
    for filename in os.listdir(directory):
        filenames.append(path.join(directory, filename))
    return filenames
