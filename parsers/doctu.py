from .base import BaseDoctorParser


class DoctuParser(BaseDoctorParser):
    source_site = "doctu"
    clinic_link_patterns = ("/clinics/", "/clinic/")
