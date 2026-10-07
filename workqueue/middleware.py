from django.conf import settings
from django.core.files.uploadhandler import FileUploadHandler, StopUpload


class LimitedUploadHandler(FileUploadHandler):
    def __init__(self, request):
        super().__init__(request)
        self.received = 0
        self.files = 0

    def new_file(self, *args, **kwargs):
        self.files += 1
        if self.files > 1:
            raise StopUpload(connection_reset=True)
        super().new_file(*args, **kwargs)

    def receive_data_chunk(self, raw_data, start):
        self.received += len(raw_data)
        if self.received > 100 * 1024 * 1024:
            raise StopUpload(connection_reset=True)
        return raw_data

    def file_complete(self, file_size):
        return None


class IntakeUploadLimitMiddleware:
    """Install stream limits before CSRF middleware reads multipart bodies."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (getattr(settings, "WORK_INTAKE_ENABLED", False) and request.method == "POST"
                and request.path == "/submit-work/uploads/"):
            request.upload_handlers.insert(0, LimitedUploadHandler(request))
        return self.get_response(request)
