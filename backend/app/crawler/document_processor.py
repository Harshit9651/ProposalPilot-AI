from bs4 import BeautifulSoup
from markdownify import markdownify as md

from app.crawler.constants import REMOVE_TAGS
from app.crawler.models import CrawledPage


class DocumentProcessor:
 

    @staticmethod
    def process(url: str, html: str) -> CrawledPage:

        soup = BeautifulSoup(html, "html.parser")

   
        for tag in REMOVE_TAGS:
            for element in soup.find_all(tag):
                element.decompose()

  
        title = ""

        if soup.title:
            title = soup.title.get_text(strip=True)

        description = ""

        meta_description = soup.find(
            "meta",
            attrs={"name": "description"},
        )

        if meta_description:
            description = meta_description.get(
                "content",
                "",
            )


        markdown = md(
            str(soup.body or soup),
            heading_style="ATX",
        )

        markdown = "\n".join(
            line.strip()
            for line in markdown.splitlines()
            if line.strip()
        )

        word_count = len(markdown.split())

        return CrawledPage(
            url=url,
            title=title,
            description=description,
            content=markdown,
            markdown=markdown,
            word_count=word_count,
            metadata={
                "title": title,
                "description": description,
            },
        )