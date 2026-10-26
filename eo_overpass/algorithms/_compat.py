"""Processing enums that moved between QGIS 3.x and 4.x."""

from qgis.core import Qgis, QgsFeatureSink, QgsProcessing, QgsProcessingParameterNumber, QgsWkbTypes


def src_type(name: str):
    st = getattr(Qgis, "ProcessingSourceType", None)
    if st is not None and hasattr(st, name):
        return getattr(st, name)
    return getattr(QgsProcessing, "Type" + name)


def num_type(name: str):           # "Double" / "Integer"
    nt = getattr(Qgis, "ProcessingNumberParameterType", None)
    if nt is not None and hasattr(nt, name):
        return getattr(nt, name)
    return getattr(QgsProcessingParameterNumber, name)


def wkb(name: str):                 # "Polygon"
    wt = getattr(Qgis, "WkbType", None)
    if wt is not None and hasattr(wt, name):
        return getattr(wt, name)
    return getattr(QgsWkbTypes, name)


def fast_insert():
    fl = getattr(QgsFeatureSink, "Flag", None)
    return fl.FastInsert if fl is not None and hasattr(fl, "FastInsert") else QgsFeatureSink.FastInsert
