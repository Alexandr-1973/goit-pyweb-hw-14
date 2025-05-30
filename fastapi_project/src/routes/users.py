import pickle
import cloudinary
import cloudinary.uploader
from fastapi import (
    APIRouter,
    Depends,
    UploadFile,
    File,
)
from fastapi_limiter.depends import RateLimiter
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi_project.src.database.db import get_db
from fastapi_project.src.database.models import User
from fastapi_project.src.schemas import UserResponse
from fastapi_project.src.services.auth import auth_service
from fastapi_project.src.conf.config import config
from fastapi_project.src.repository import users as repositories_users

router = APIRouter(prefix="/users", tags=["users"])
cloudinary.config(
    cloud_name=config.CLOUDINARY_NAME,
    api_key=config.CLOUDINARY_API_KEY,
    api_secret=config.CLOUDINARY_API_SECRET,
    secure=True,
)


@router.get(
    "/me",
    response_model=UserResponse,
    dependencies=[Depends(RateLimiter(times=1, seconds=20))],
)
async def get_current_user(user: User = Depends(auth_service.get_current_user)):
    """
    Retrieve the current authenticated user.

    :param user: The current authenticated user, resolved from the access token.
    :type user: User
    :return: The authenticated user's details.
    :rtype: UserResponse
    """
    return user


@router.patch(
    "/avatar",
    response_model=UserResponse,
    dependencies=[Depends(RateLimiter(times=1, seconds=60, identifier=auth_service.get_email_from_request))],
)
async def update_user_avatar(
    file: UploadFile = File(),
    user: User = Depends(auth_service.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload and update the avatar for the current user.

    The image is uploaded to Cloudinary and a resized (250x250) image URL is stored in the database.

    :param file: The uploaded avatar image file.
    :type file: UploadFile
    :param user: The current authenticated user.
    :type user: User
    :param db: Database session.
    :type db: AsyncSession
    :return: The updated user with the new avatar URL.
    :rtype: UserResponse
    """
    public_id = f"Web16/{user.email}"
    res = cloudinary.uploader.upload(file.file, public_id=public_id, owerite=True)
    res_url = cloudinary.CloudinaryImage(public_id).build_url(
        width=250, height=250, crop="fill", version=res.get("version")
    )
    user = await repositories_users.update_avatar_url(user.email, res_url, db)
    auth_service.cache.set(user.email, pickle.dumps(user))
    auth_service.cache.expire(user.email, 300)
    return user