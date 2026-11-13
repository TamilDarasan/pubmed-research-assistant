
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

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
    
