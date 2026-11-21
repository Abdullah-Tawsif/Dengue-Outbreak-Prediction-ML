"""FastAPI application entry point."""

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse

from src.backend.database import Base, engine

app = FastAPI(title="Dengue Risk Prediction")

# Serve frontend assets from the project's static directory.
app.mount("/static", StaticFiles(directory="static"), name="static")

# Load HTML templates from the project's templates directory.
templates = Jinja2Templates(directory="templates")



@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="home/index.html",
        context={}
    )


@app.get("/login")
async def login(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="auth/login/login.html",
        context={}
    )


@app.get("/register")
async def register(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="auth/register/register.html",
        context={}
    )


@app.get("/user_dashboard")
async def user_dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="user/user_dashboard.html",
        context={}
    )


@app.get("/admin_dashboard")
async def admin_dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="admin/admin_dashboard.html",
        context={}
    )


# Create ORM tables in the configured database.
Base.metadata.create_all(bind=engine)


# Vercel Python Function ASGI adapter.
from mangum import Mangum
handler = Mangum(app)