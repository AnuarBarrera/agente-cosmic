from .content_tools import GeneratePostTool, WriteTextTool, GenerateShortScriptTool
from .report_tools import GenerateMonthlyReportTool
from .whisper_tool import TranscribeAudioTool
from .maps_tools import ProspectMapsTool
from .browser_tools import GetPostStatsTool
from .login_tool import BrowserLoginTool
from .search_tools import WebSearchTool
from .document_tools import GenerateDocumentTool
from .prospect_tools import ProspectResearchTool
from .image_tools import GeneratePostImageTool
from .media_tools import GenerateAudioTool, GenerateVideoTool
from .rag_tools import RAGUploadTool, RAGQueryTool

_registry = None


def get_registry() -> dict:
    global _registry
    if _registry is None:
        _registry = {
            'generate_post': GeneratePostTool(),
            'write_text': WriteTextTool(),
            'generate_short_script': GenerateShortScriptTool(),
            'generate_monthly_report': GenerateMonthlyReportTool(),
            'transcribe_audio': TranscribeAudioTool(),
            'prospect_maps': ProspectMapsTool(),
            'get_post_stats': GetPostStatsTool(),
            'browser_login': BrowserLoginTool(),
            'web_search': WebSearchTool(),
            'generate_document': GenerateDocumentTool(),
            'prospect_research': ProspectResearchTool(),
            'generate_post_image': GeneratePostImageTool(),
            'generate_audio': GenerateAudioTool(),
            'generate_video': GenerateVideoTool(),
            'rag_upload': RAGUploadTool(),
            'rag_query': RAGQueryTool(),
        }
    return _registry


def get_tool(name: str):
    return get_registry().get(name)
