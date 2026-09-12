"""Small storefront gateway using the inherited Catalog and Cart gRPC services."""
import json
import logging
import os
import re
import time
from pathlib import Path

import grpc
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from google.protobuf.json_format import MessageToDict
from pydantic import BaseModel, Field
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

import demo_pb2 as pb
import demo_pb2_grpc as rpc

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("lab-web")
if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.grpc import GrpcInstrumentorClient
    provider = TracerProvider(resource=Resource.create({"service.name": "web"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(insecure=True)))
    trace.set_tracer_provider(provider)
    GrpcInstrumentorClient().instrument()

catalog = rpc.ProductCatalogServiceStub(grpc.insecure_channel(os.getenv("CATALOG_ADDR", "catalog:3550")))
cart = rpc.CartServiceStub(grpc.insecure_channel(os.getenv("CART_ADDR", "cart:7070")))
app = FastAPI(title="Orbit Shop")
if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,metrics")
requests = Counter("lab_http_requests_total", "HTTP requests", ["route", "status"])
latency = Histogram("lab_http_request_seconds", "HTTP latency", ["route"])


@app.middleware("http")
async def observe(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    route = getattr(request.scope.get("route"), "path", "unmatched")
    if route not in ("/healthz", "/metrics"):
        requests.labels(route, str(response.status_code)).inc()
        latency.labels(route).observe(time.monotonic() - start)
        log.info(json.dumps({"route": route, "status": response.status_code,
                             "duration_ms": round((time.monotonic()-start)*1000)}))
    return response


def invoke(method, message):
    try:
        return MessageToDict(method(message, timeout=5), preserving_proto_field_name=True)
    except grpc.RpcError as exc:
        log.error("dependency_rpc_failed code=%s detail=%s", exc.code(), exc.details())
        code = 404 if exc.code() == grpc.StatusCode.NOT_FOUND else 503
        raise HTTPException(code, "Service temporarily unavailable" if code == 503 else "Product not found") from exc


def user_id(request):
    value = request.cookies.get("lab_session", "")
    if not re.fullmatch(r"[a-f0-9-]{36}", value):
        raise HTTPException(400, "A valid browser session is required")
    return value


class Item(BaseModel):
    product_id: str = Field(min_length=1, max_length=64)
    quantity: int = Field(ge=1, le=20)


@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/products")
def products():
    return {"products": invoke(catalog.ListProducts, pb.Empty()).get("products", [])}


@app.get("/api/products/{product_id}")
def product(product_id: str):
    return invoke(catalog.GetProduct, pb.GetProductRequest(id=product_id))


@app.get("/api/cart")
def get_cart(request: Request):
    return {"items": invoke(cart.GetCart, pb.GetCartRequest(user_id=user_id(request))).get("items", [])}


@app.post("/api/cart")
def add_cart(item: Item, request: Request):
    invoke(catalog.GetProduct, pb.GetProductRequest(id=item.product_id))
    return invoke(cart.AddItemAndGetCart, pb.AddItemRequest(user_id=user_id(request), item=pb.CartItem(**item.model_dump())))


@app.delete("/api/cart/{product_id}")
def remove_item(product_id: str, request: Request):
    return invoke(cart.RemoveItem, pb.RemoveItemRequest(user_id=user_id(request), product_id=product_id))


@app.delete("/api/cart")
def empty_cart(request: Request):
    invoke(cart.EmptyCart, pb.EmptyCartRequest(user_id=user_id(request)))
    return {"items": []}


@app.get("/images/{filename}")
async def product_image(filename: str):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", filename):
        raise HTTPException(400)
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            result = await client.get(os.getenv("IMAGE_URL", "http://image-provider:8080") + "/images/products/" + filename)
        return Response(result.content, status_code=result.status_code,
                        media_type=result.headers.get("content-type", "image/jpeg"))
    except httpx.RequestError as exc:
        raise HTTPException(503, "Image service unavailable") from exc


static = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static), name="static")


@app.get("/")
def home():
    return FileResponse(static / "index.html")
