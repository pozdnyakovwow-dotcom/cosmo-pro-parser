from .base import BaseDoctorParser


class NaPopravkuParser(BaseDoctorParser):
    source_site = "napopravku"
    clinic_link_patterns = ("/clinics/", "/doctor-profile/")
