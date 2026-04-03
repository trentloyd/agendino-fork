from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app import depends
from controllers.RAGController import RAGController

router = APIRouter()


@router.get("/knowledge", response_class=HTMLResponse)
def knowledge_home(request: Request, rag_controller: RAGController = Depends(depends.get_rag_controller)):
    return rag_controller.home(request)
