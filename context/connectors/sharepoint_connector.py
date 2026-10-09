"""
SharePoint source connector — STUB.

Interface is fully defined. Implementation requires:
  - Microsoft Graph API credentials (client_id, client_secret, tenant_id)
  - The `msal` package for OAuth2 authentication
  - The `office365-rest-python-client` package or direct Graph API calls

Configuration (claude.md) — when implemented
─────────────────────────────────────────────
sources:
  - type: sharepoint
    site_url: https://myorg.sharepoint.com/sites/Engineering
    library: Documents              # document library name
    folder: /Architecture/ADRs      # optional: scope to a folder
    include_extensions:
      - .docx
      - .md
      - .pdf

Required secrets — when implemented
────────────────────────────────────
SHAREPOINT_CLIENT_ID      — Azure App Registration client ID
SHAREPOINT_CLIENT_SECRET  — Azure App Registration client secret
SHAREPOINT_TENANT_ID      — Azure AD tenant ID

Implementation guide
────────────────────
1. Authenticate via MSAL:
   app = msal.ConfidentialClientApplication(client_id, client_secret, authority)
   token = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])

2. List files via Microsoft Graph API:
   GET https://graph.microsoft.com/v1.0/sites/{site_id}/drives/{drive_id}/root/children

3. Fetch file content:
   GET https://graph.microsoft.com/v1.0/sites/{site_id}/drives/{drive_id}/items/{item_id}/content

4. Get changes since timestamp (incremental sync):
   Use the Graph API delta query:
   GET https://graph.microsoft.com/v1.0/sites/{site_id}/drives/{drive_id}/root/delta

5. For .docx files, extract text using python-docx before indexing.
   For .pdf files, use pdfplumber or PyMuPDF.

References
──────────
Microsoft Graph API docs: https://learn.microsoft.com/en-us/graph/api/overview
SharePoint REST API:       https://learn.microsoft.com/en-us/sharepoint/dev/sp-add-ins/get-to-know-the-sharepoint-rest-service
MSAL Python:               https://github.com/AzureAD/microsoft-authentication-library-for-python
"""

from datetime import datetime

from context.connectors.base import DocumentMetadata, SourceConnector


class SharePointConnector(SourceConnector):
    """
    Indexes documents from a SharePoint document library.

    NOT YET IMPLEMENTED — raises NotImplementedError on all methods.
    See module docstring for implementation guide.
    """

    def __init__(
        self,
        site_url: str,
        library: str = "Documents",
        folder: str = "/",
        client_id: str | None = None,
        client_secret: str | None = None,
        tenant_id: str | None = None,
        include_extensions: set[str] | None = None,
    ) -> None:
        self.site_url    = site_url
        self.library     = library
        self.folder      = folder
        self.client_id   = client_id
        self.client_secret = client_secret
        self.tenant_id   = tenant_id
        self.include_extensions = include_extensions or {".docx", ".md", ".pdf", ".txt"}

    def get_identifier(self) -> str:
        return f"sharepoint::{self.site_url}/{self.library}{self.folder}"

    def health_check(self) -> tuple[bool, str]:
        return False, (
            "SharePointConnector is not yet implemented. "
            "See context/connectors/sharepoint_connector.py for the implementation guide."
        )

    def list_documents(self) -> list[DocumentMetadata]:
        raise NotImplementedError(
            "SharePointConnector.list_documents() is not yet implemented. "
            "See module docstring for implementation guide."
        )

    def fetch_document(self, document_id: str) -> str:
        raise NotImplementedError(
            "SharePointConnector.fetch_document() is not yet implemented."
        )

    def get_changes_since(self, since: datetime) -> list[DocumentMetadata]:
        raise NotImplementedError(
            "SharePointConnector.get_changes_since() is not yet implemented. "
            "Use the Microsoft Graph delta query API — see module docstring."
        )

    def supports_incremental(self) -> bool:
        return False  # update to True once get_changes_since is implemented
