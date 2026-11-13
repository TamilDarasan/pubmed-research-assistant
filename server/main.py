
"""PubMed MCP server using NCBI E-utilities."""
import os
import httpx
import xml.etree.ElementTree as ET
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "pubmed-research-assistant",
    stateless_http=True,
    host="0.0.0.0",
    port=int(os.getenv("PORT", "8000"))
)
BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

async def request(endpoint: str, params: dict):
    params.setdefault("tool", "pubmed-research-assistant")
    params.setdefault("email", os.getenv("NCBI_TOOL_EMAIL", ""))
    if os.getenv("NCBI_API_KEY"):
        params["api_key"] = os.environ["NCBI_API_KEY"]

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{BASE}/{endpoint}", params=params)
        response.raise_for_status()
        return response

@mcp.tool()
async def search_pubmed(
    query: str,
    retmax: int = 20,
    date_from: str = "",
    date_to: str = "",
    article_type: str = ""
) -> dict:
    """Search PubMed and return verified PMIDs."""
    if not query.strip():
        raise ValueError("query must not be empty")

    retmax = max(1, min(int(retmax), 100))
    terms = [f"({query})"]

    if article_type.strip():
        terms.append(f'("{article_type}"[Publication Type])')

    params = {
        "db": "pubmed",
        "term": " AND ".join(terms),
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

    data = (await request("esearch.fcgi", params)).json()
    result = data.get("esearchresult", {})

    return {
        "query": params["term"],
        "count": int(result.get("count", 0)),
        "pmids": result.get("idlist", []),
        "retmax": retmax
    }

@mcp.tool()
async def fetch_pubmed_articles(pmids: list[str]) -> list[dict]:
    """Fetch article metadata and abstracts for up to 50 PubMed IDs."""
    clean = [
        str(x).strip()
        for x in pmids
        if str(x).strip().isdigit()
    ][:50]

    if not clean:
        raise ValueError("Provide numeric PubMed IDs")

    response = await request(
        "efetch.fcgi",
        {
            "db": "pubmed",
            "id": ",".join(clean),
            "retmode": "xml"
        }
    )

    root = ET.fromstring(response.text)
    records = []

    for article in root.findall(".//PubmedArticle"):
        pmid = article.findtext(".//PMID", default="")
        title_node = article.find(".//ArticleTitle")
        title = "".join(title_node.itertext()) if title_node is not None else ""

        authors = []
        for author in article.findall(".//AuthorList/Author"):
            collective = author.findtext("CollectiveName")
            last = author.findtext("LastName", default="")
            initials = author.findtext("Initials", default="")
            name = collective or f"{last} {initials}".strip()
            if name:
                authors.append(name)

        abstract_parts = []
        for part in article.findall(".//Abstract/AbstractText"):
            label = part.attrib.get("Label", "")
            value = "".join(part.itertext())
            abstract_parts.append(
                f"{label}: {value}" if label else value
            )

        journal = article.findtext(".//Journal/Title", default="")
        year = article.findtext(".//PubDate/Year", default="")

        doi = ""
        for aid in article.findall(".//PubmedData/ArticleIdList/ArticleId"):
            if aid.attrib.get("IdType") == "doi":
                doi = aid.text or ""

        records.append({
            "pmid": pmid,
            "title": title,
            "authors": authors,
            "journal": journal,
            "year": year,
            "doi": doi,
            "abstract": "\n".join(abstract_parts),
            "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        })

    return records
# ==========================================
# EUROPE PMC INTEGRATION
# ==========================================

EUROPE_PMC_BASE = (
    "https://www.ebi.ac.uk/europepmc/webservices/rest"
)


@mcp.tool()
async def search_europe_pmc(
    query: str,
    limit: int = 20,
    open_access_only: bool = False,
    sort_recent: bool = True
) -> dict:
    """
    Search Europe PMC for biomedical research papers.

    Supports abstracts, publication metadata,
    preprints and open-access discovery.
    """

    if not query.strip():
        raise ValueError("Search query cannot be empty")

    limit = max(1, min(int(limit), 100))

    search_query = query.strip()

    if open_access_only:
        search_query += " AND OPEN_ACCESS:Y"

    if sort_recent:
        search_query += " sort_date:y"

    params = {
        "query": search_query,
        "format": "json",
        "resultType": "core",
        "pageSize": limit
    }

    async with httpx.AsyncClient(timeout=40) as client:

        response = await client.get(
            f"{EUROPE_PMC_BASE}/search",
            params=params
        )

        response.raise_for_status()

        data = response.json()

    results = []

    for item in data.get("resultList", {}).get("result", []):

        pmcid = item.get("pmcid", "")

        results.append({

            "source": "Europe PMC",

            "id": item.get("id", ""),

            "source_database": item.get("source", ""),

            "pmid": item.get("pmid", ""),

            "pmcid": pmcid,

            "title": item.get("title", ""),

            "authors": item.get("authorString", ""),

            "journal": item.get("journalTitle", ""),

            "year": item.get("pubYear", ""),

            "publication_date": item.get(
                "firstPublicationDate", ""
            ),

            "doi": item.get("doi", ""),

            "abstract": item.get("abstractText", ""),

            "publication_type": item.get("pubTypeList", {}).get(
                "pubType", []
            ),

            "is_open_access": item.get(
                "isOpenAccess", "N"
            ) == "Y",

            "has_full_text": item.get(
                "inEPMC", "N"
            ) == "Y",

            "pubmed_url": (
                f"https://pubmed.ncbi.nlm.nih.gov/"
                f"{item.get('pmid')}/"
                if item.get("pmid") else ""
            ),

            "europe_pmc_url": (
                f"https://europepmc.org/article/"
                f"{item.get('source')}/"
                f"{item.get('id')}"
            )
        })

    return {

        "database": "Europe PMC",

        "search_query": search_query,

        "total_results": data.get("hitCount", 0),

        "returned_results": len(results),

        "articles": results
    }


@mcp.tool()
async def fetch_europe_pmc_full_text(
    pmcid: str
) -> dict:
    """
    Retrieve full text XML for an eligible
    Europe PMC open-access article.

    Requires a valid PMCID such as PMC1234567.
    """

    pmcid = pmcid.strip().upper()

    if not pmcid.startswith("PMC"):
        raise ValueError("Provide a valid PMCID, e.g. PMC1234567")

    if not pmcid[3:].isdigit():
        raise ValueError("Invalid PMCID format")

    async with httpx.AsyncClient(timeout=60) as client:

        response = await client.get(
            f"{EUROPE_PMC_BASE}/{pmcid}/fullTextXML"
        )

        if response.status_code == 404:
            return {
                "pmcid": pmcid,
                "available": False,
                "message": "Full text is unavailable."
            }

        response.raise_for_status()

    root = ET.fromstring(response.text)

    sections = []

    for element in root.iter():

        if element.tag.split("}")[-1] in (
            "article-title",
            "abstract",
            "title",
            "p"
        ):

            text_content = " ".join(
                "".join(element.itertext()).split()
            )

            if text_content:
                sections.append(text_content)

    full_text = "\n\n".join(sections)

    return {

        "pmcid": pmcid,

        "available": True,

        "source": "Europe PMC",

        "full_text": full_text[:40000],

        "truncated": len(full_text) > 40000,

        "source_url": (
            f"https://europepmc.org/articles/{pmcid}"
        )
    }


@mcp.tool()
async def find_open_access_articles(
    query: str,
    limit: int = 20
) -> dict:
    """
    Find biomedical articles with open-access
    full-text availability.
    """

    return await search_europe_pmc(
        query=query,
        limit=limit,
        open_access_only=True,
        sort_recent=True
    )

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
    
