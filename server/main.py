"""Pharma Research Copilot - PubMed and Europe PMC MCP Server."""

import os
import re
import httpx
import xml.etree.ElementTree as ET

from mcp.server.fastmcp import FastMCP


# ============================================================
# MCP SERVER CONFIGURATION
# ============================================================

mcp = FastMCP(
    "pubmed-research-assistant",
    stateless_http=True,
    host="0.0.0.0",
    port=int(os.getenv("PORT", "8000"))
)


# ============================================================
# API CONFIGURATION
# ============================================================

PUBMED_BASE = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
)

EUROPE_PMC_BASE = (
    "https://www.ebi.ac.uk/europepmc/webservices/rest"
)


# ============================================================
# COMMON HTTP REQUEST FUNCTIONS
# ============================================================

async def request(endpoint: str, params: dict):
    """Send requests to the NCBI E-utilities API."""

    params = params.copy()

    params.setdefault(
        "tool",
        "pubmed-research-assistant"
    )

    email = os.getenv("NCBI_TOOL_EMAIL")

    if email:
        params["email"] = email

    api_key = os.getenv("NCBI_API_KEY")

    if api_key:
        params["api_key"] = api_key

    async with httpx.AsyncClient(timeout=30) as client:

        response = await client.get(
            f"{PUBMED_BASE}/{endpoint}",
            params=params
        )

        response.raise_for_status()

        return response


async def europe_pmc_request(
    endpoint: str,
    params: dict | None = None
):
    """Send requests to the Europe PMC REST API."""

    async with httpx.AsyncClient(timeout=30) as client:

        response = await client.get(
            f"{EUROPE_PMC_BASE}/{endpoint}",
            params=params or {}
        )

        response.raise_for_status()

        return response


# ============================================================
# PUBLICATION TYPE NORMALIZATION
# ============================================================

def normalize_publication_type(value):
    """
    Normalize publication types into a predictable
    list of strings.
    """

    if value is None:
        return []

    if isinstance(value, str):
        return [value] if value.strip() else []

    if isinstance(value, dict):
        value = value.get("pubType", [])

        if isinstance(value, str):
            return [value] if value.strip() else []

    if isinstance(value, list):

        normalized = []

        for item in value:

            if isinstance(item, str):

                if item.strip():
                    normalized.append(item)

            elif isinstance(item, dict):

                pub_type = (
                    item.get("type")
                    or item.get("name")
                    or item.get("value")
                )

                if isinstance(pub_type, str) and pub_type.strip():
                    normalized.append(pub_type)

        return normalized

    return []


# ============================================================
# PUBMED SEARCH
# ============================================================

@mcp.tool()
async def search_pubmed(
    query: str,
    retmax: int = 20,
    date_from: str = "",
    date_to: str = "",
    article_type: str = ""
) -> dict:
    """
    Search PubMed and return matching PMIDs.

    Supports:
    - Keyword searching
    - Publication date filtering
    - Article type filtering
    """

    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must not be empty")

    retmax = max(1, min(int(retmax), 100))

    terms = [f"({query.strip()})"]

    if article_type and article_type.strip():

        terms.append(
            f'("{article_type.strip()}"[Publication Type])'
        )

    search_term = " AND ".join(terms)

    params = {
        "db": "pubmed",
        "term": search_term,
        "retmode": "json",
        "retmax": retmax,
        "sort": "relevance"
    }

    if date_from or date_to:

        params.update({
            "datetype": "pdat",
            "mindate": date_from or "1900",
            "maxdate": date_to or "3000"
        })

    data = (
        await request("esearch.fcgi", params)
    ).json()

    result = data.get("esearchresult", {})

    return {
        "query": search_term,
        "count": int(result.get("count", 0)),
        "pmids": result.get("idlist", []),
        "retmax": retmax
    }


# ============================================================
# PUBMED ARTICLE RETRIEVAL
# ============================================================

@mcp.tool()
async def fetch_pubmed_articles(
    pmids: list[str]
) -> list[dict]:
    """
    Retrieve metadata and abstracts for PubMed IDs.
    """

    if not isinstance(pmids, list):
        raise ValueError("pmids must be a list")

    clean = [
        str(x).strip()
        for x in pmids
        if str(x).strip().isdigit()
    ][:50]

    if not clean:
        raise ValueError("Provide valid numeric PubMed IDs")

    response = await request(
        "efetch.fcgi",
        {
            "db": "pubmed",
            "id": ",".join(clean),
            "retmode": "xml"
        }
    )

    try:
        root = ET.fromstring(response.text)

    except ET.ParseError as exc:
        raise ValueError(
            "PubMed returned invalid XML."
        ) from exc

    records = []

    for article in root.findall(".//PubmedArticle"):

        pmid = article.findtext(
            ".//PMID",
            default=""
        )

        title_node = article.find(".//ArticleTitle")

        title = (
            "".join(title_node.itertext())
            if title_node is not None
            else ""
        )

        # Authors
        authors = []

        for author in article.findall(
            ".//AuthorList/Author"
        ):

            collective = author.findtext("CollectiveName")

            last = author.findtext(
                "LastName",
                default=""
            )

            initials = author.findtext(
                "Initials",
                default=""
            )

            name = collective or f"{last} {initials}".strip()

            if name:
                authors.append(name)

        # Abstract
        abstract_parts = []

        for part in article.findall(
            ".//Abstract/AbstractText"
        ):

            label = part.attrib.get("Label", "")

            value = "".join(part.itertext())

            abstract_parts.append(
                f"{label}: {value}"
                if label
                else value
            )

        # Journal
        journal = article.findtext(
            ".//Journal/Title",
            default=""
        )

        # Publication year
        year = article.findtext(
            ".//PubDate/Year",
            default=""
        )

        # DOI extraction
        doi = ""

        for aid in article.findall(
            ".//ArticleIdList/ArticleId"
        ):

            if aid.attrib.get("IdType") == "doi":

                doi = aid.text or ""
                break

        records.append({

            "pmid": pmid,

            "title": title,

            "authors": authors,

            "journal": journal,

            "year": year,

            "doi": doi,

            "abstract": "\n".join(abstract_parts),

            "pubmed_url": (
                f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
                if pmid
                else ""
            )

        })

    return records


# ============================================================
# EUROPE PMC SEARCH
# ============================================================

@mcp.tool()
async def search_europe_pmc(
    query: str,
    limit: int = 20,
    open_access_only: bool = False,
    sort_recent: bool = True
) -> dict:
    """
    Search Europe PMC for scientific literature.

    Supports:
    - Keyword searching
    - Open-access filtering
    - Recent publication sorting
    - Article metadata
    - Abstract retrieval
    """

    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must not be empty")

    limit = max(1, min(int(limit), 100))

    search_query = query.strip()

    if open_access_only:
        search_query += " AND OPEN_ACCESS:Y"

    params = {
        "query": search_query,
        "format": "json",
        "resultType": "core",
        "pageSize": limit
    }

    if sort_recent:
        params["sort"] = "P_PDATE_D desc"

    data = (
        await europe_pmc_request(
            "search",
            params
        )
    ).json()

    # --------------------------------------------
    # Validate API response structure
    # --------------------------------------------

    if not isinstance(data, dict):
        raise ValueError(
            "Europe PMC returned an unexpected response."
        )

    result_list = data.get("resultList")

    if not isinstance(result_list, dict):
        results = []
    else:
        results = result_list.get("result") or []

    if not isinstance(results, list):
        results = []

    articles = []

    for item in results:

        if not isinstance(item, dict):
            continue

        # ----------------------------------------
        # Safe journal extraction
        # ----------------------------------------

        journal_info = item.get("journalInfo")

        journal = (
            journal_info.get("journal")
            if isinstance(journal_info, dict)
            else None
        )

        journal_title = (
            journal.get("title", "")
            if isinstance(journal, dict)
            else ""
        )

        final_journal = (
            item.get("journalTitle")
            or journal_title
        )

        # ----------------------------------------
        # Normalize publication type
        # ----------------------------------------

        publication_type = normalize_publication_type(
            item.get("pubTypeList")
        )

        # ----------------------------------------
        # Authors
        # ----------------------------------------

        author_list_data = item.get("authorList")

        author_list = (
            author_list_data.get("author", [])
            if isinstance(author_list_data, dict)
            else []
        )

        authors = []

        if isinstance(author_list, list):

            for author in author_list:

                if isinstance(author, dict):

                    full_name = (
                        author.get("fullName")
                        or author.get("authorString")
                        or ""
                    )

                    if full_name:
                        authors.append(full_name)

        # ----------------------------------------
        # Identifiers
        # ----------------------------------------

        pmid = item.get("pmid", "") or ""

        pmcid = item.get("pmcid", "") or ""

        doi = item.get("doi", "") or ""

        # ----------------------------------------
        # Availability flags
        # ----------------------------------------

        is_open_access = (
            str(item.get("isOpenAccess", "N")).upper()
            == "Y"
        )

        in_europe_pmc = (
            str(item.get("inEPMC", "N")).upper()
            == "Y"
        )

        in_pmc = (
            str(item.get("inPMC", "N")).upper()
            == "Y"
        )

        # Indicates likely retrievable PMC XML.
        # Actual availability must still be checked
        # by calling the full-text endpoint.

        has_full_text = (
            in_pmc
            and is_open_access
            and bool(pmcid)
        )

        # ----------------------------------------
        # Final article record
        # ----------------------------------------

        articles.append({

            "source": "Europe PMC",

            "id": item.get("id", ""),

            "source_database": item.get("source", ""),

            "pmid": pmid,

            "pmcid": pmcid,

            "title": item.get("title", ""),

            "authors": authors,

            "journal": final_journal,

            "year": item.get("pubYear", ""),

            "publication_date": item.get(
                "firstPublicationDate",
                ""
            ),

            "doi": doi,

            "abstract": item.get("abstractText", ""),

            "publication_type": publication_type,

            "is_open_access": is_open_access,

            "in_europe_pmc": in_europe_pmc,

            "in_pmc": in_pmc,

            "has_full_text": has_full_text,

            "pubmed_url": (
                f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
                if pmid
                else ""
            ),

            "europe_pmc_url": (
                f"https://europepmc.org/article/"
                f"{item.get('source', '')}/{item.get('id', '')}"
            )

        })

    return {

        "query": search_query,

        "count": data.get("hitCount", 0),

        "returned": len(articles),

        "articles": articles

    }


# ============================================================
# EUROPE PMC FULL-TEXT RETRIEVAL
# ============================================================

@mcp.tool()
async def fetch_europe_pmc_full_text(
    pmcid: str
) -> dict:
    """
    Retrieve legally available PMC full-text XML
    through the Europe PMC REST API.
    """

    if not isinstance(pmcid, str):
        raise ValueError("pmcid must be a string")

    pmcid = pmcid.strip().upper()

    # Accept standard and versioned PMCIDs.
    if not re.fullmatch(r"PMC\d+(?:\.\d+)?", pmcid):

        raise ValueError(
            "Invalid PMCID format. Example: PMC1234567"
        )

    try:

        # Correct endpoint:
        # /PMC1234567/fullTextXML

        response = await europe_pmc_request(
            f"{pmcid}/fullTextXML"
        )

    except httpx.HTTPStatusError as exc:

        if exc.response.status_code == 404:

            return {

                "pmcid": pmcid,

                "available": False,

                "message": (
                    "Full-text XML is not available "
                    "for this article."
                )

            }

        raise

    # --------------------------------------------
    # Parse XML safely
    # --------------------------------------------

    try:

        root = ET.fromstring(response.text)

    except ET.ParseError as exc:

        raise ValueError(
            "Europe PMC returned an invalid full-text XML response."
        ) from exc

    # --------------------------------------------
    # Extract article title and paragraphs
    # --------------------------------------------

    sections = []

    for element in root.iter():

        tag = element.tag.split("}")[-1]

        if tag in ("article-title", "p"):

            text_content = " ".join(
                "".join(element.itertext()).split()
            )

            if text_content:
                sections.append(text_content)

    full_text = "\n\n".join(sections)

    max_length = 40000

    return {

        "pmcid": pmcid,

        "available": True,

        "source": "Europe PMC",

        "full_text": full_text[:max_length],

        "truncated": len(full_text) > max_length,

        "source_url": (
            f"https://europepmc.org/articles/{pmcid}"
        )

    }


# ============================================================
# OPEN-ACCESS ARTICLE SEARCH
# ============================================================

@mcp.tool()
async def find_open_access_articles(
    query: str,
    limit: int = 20
) -> dict:
    """
    Find recent open-access research articles
    through Europe PMC.
    """

    return await search_europe_pmc(
        query=query,
        limit=limit,
        open_access_only=True,
        sort_recent=True
    )


# ============================================================
# SERVER STARTUP
# ============================================================

if __name__ == "__main__":

    mcp.run(
        transport="streamable-http"
    )
