from .base import BaseDoctorParser


class DocDocParser(BaseDoctorParser):
    source_site = "docdoc"
    clinic_link_patterns = ("/clinic/",)
