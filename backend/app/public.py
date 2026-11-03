"""Business response projections. Storage and authentication retain their own contracts."""
from fastapi.encoders import jsonable_encoder
from .scope import redact_private


PRIVATE_KEYS={'authorization_code','access_token','refresh_token','api_key','client_secret','password'}
INTERNAL_KEYS={'requests','lease'}


def public_business(value):
    original=jsonable_encoder(value)
    masked=redact_private(original)
    def project(raw,safe):
        if isinstance(raw,dict):
            # Preserve the business schema: presence markers already in preview nodes
            # survive, but never add fields to a nested StructureSpec or SourceRef.
            return {key:project(item,safe[key]) for key,item in raw.items()
                    if key.lower() not in PRIVATE_KEYS|INTERNAL_KEYS and key in safe}
        if isinstance(raw,list):return [project(item,clean) for item,clean in zip(raw,safe)]
        return safe
    return project(original,masked)


def public_agent_state(response,draft,request_id):
    return public_business({'response':{**response,'request_id':request_id},'draft':draft})
