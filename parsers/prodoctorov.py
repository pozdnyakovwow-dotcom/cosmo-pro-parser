from .base import BaseDoctorParser


class ProdoctorovParser(BaseDoctorParser):
    source_site = "prodoctorov"
    clinic_link_patterns = ("/lpu/",)

    def _extract_clinic(self, soup):
        for anchor in soup.select('a[href*="/lpu/"]'):
            text = self._safe_text(anchor)
            if text and text.lower() != "клиники":
                return text
        return super()._extract_clinic(soup)
