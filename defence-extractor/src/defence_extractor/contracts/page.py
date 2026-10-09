"""A2 Page intelligence output."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class SectionRole(StrEnum):
    product_overview = "product_overview"
    specifications = "specifications"
    features_capabilities = "features_capabilities"
    variants = "variants"
    narrative = "narrative"
    news_body = "news_body"
    comparison = "comparison"
    catalogue_listing = "catalogue_listing"
    related_products = "related_products"
    accessories = "accessories"
    company_info = "company_info"
    navigation = "navigation"
    footer = "footer"
    contact_legal = "contact_legal"
    other = "other"


#: sections the discovery agents read
EXTRACTABLE_ROLES = {
    SectionRole.product_overview,
    SectionRole.specifications,
    SectionRole.features_capabilities,
    SectionRole.variants,
    SectionRole.narrative,
    SectionRole.news_body,
    SectionRole.comparison,
    SectionRole.catalogue_listing,
    SectionRole.accessories,
    SectionRole.other,
}


#: roles that make a NON-main block (sidebar, related content, reader comments) readable: positive product content only
#: ("other" / narrative sections outside the main content are typically comment threads and teasers)
NONMAIN_EXTRACTABLE_ROLES = {
    SectionRole.product_overview,
    SectionRole.specifications,
    SectionRole.features_capabilities,
    SectionRole.variants,
    SectionRole.comparison,
    SectionRole.catalogue_listing,
    SectionRole.accessories,
}


class SectionAssessment(BaseModel):
    section_id: str
    role: SectionRole
    spec_density: str = "low"  # high | medium | low | none
    subjects: list[str] = Field(default_factory=list)


class PageMap(BaseModel):
    page_type: str = "unknown"
    page_scope: str = "single_product"
    source_company_candidates: list[str] = Field(default_factory=list)
    primary_subject_candidates: list[str] = Field(default_factory=list)
    sections: list[SectionAssessment] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    confidence: float = 0.5

    def role_of(self, section_id: str) -> SectionRole | None:
        for s in self.sections:
            if s.section_id == section_id:
                return s.role
        return None
