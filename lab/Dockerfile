FROM python:3.12-slim
WORKDIR /app
COPY lab/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY techx-corp-platform/pb/demo.proto /app/demo.proto
RUN python -m grpc_tools.protoc -I /app --python_out=/app --grpc_python_out=/app /app/demo.proto
COPY lab/web/ /app/
COPY lab/tools/ /app/tools/
USER 65532:65532
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
