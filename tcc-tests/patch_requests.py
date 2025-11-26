# import requests

# class PatchedSession(requests.Session):
#     def request(self, *args, **kwargs):
#         print(">> patch_requests carregado com sucesso")
#         headers = kwargs.setdefault("headers", {})
#         headers["Accept-Encoding"] = "identity"
#         return super().request(*args, **kwargs)

# requests.Session = PatchedSession

# Works for nova-act 2.3.18.0





