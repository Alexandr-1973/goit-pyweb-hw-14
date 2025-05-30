from fastapi import Form, APIRouter, HTTPException, Depends, status, BackgroundTasks, Request, Response
from fastapi.security import OAuth2PasswordRequestForm, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.templating import Jinja2Templates
from fastapi_project.src.database.db import get_db
from fastapi_project.src.repository import users as repositories_users
from fastapi_project.src.schemas import UserSchema, TokenSchema, UserResponse, RequestEmail
from fastapi_project.src.services.auth import auth_service
from fastapi_project.src.services.email import send_email, send_rp_email

router = APIRouter(prefix='/auth', tags=['auth'])
templates = Jinja2Templates(directory="fastapi_project/src/services/templates")
get_refresh_token = HTTPBearer()

@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def signup(body: UserSchema, bt: BackgroundTasks, request: Request, db: AsyncSession = Depends(get_db)):
    """
    Register a new user account.

    :param body: User registration data.
    :param bt: Background task manager.
    :param request: HTTP request object.
    :param db: Database session.
    :return: Created user details.
    :raises HTTPException: If the email already exists.
    """
    exist_user = await repositories_users.get_user_by_email(body.email, db)
    if exist_user:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Account already exists')
    body.password = auth_service.get_password_hash(body.password)
    new_user = await repositories_users.create_user(body, db)
    bt.add_task(send_email, new_user.email, new_user.username, str(request.base_url))
    return new_user

@router.post("/login",  response_model=TokenSchema)
async def login(body: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    """
    Authenticate a user and generate JWT tokens.

    :param body: Login credentials.
    :param db: Database session.
    :return: Access and refresh tokens.
    :raises HTTPException: If authentication fails or email is not confirmed.
    """
    user = await repositories_users.get_user_by_email(body.username, db)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email")
    if not user.confirmed:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email not confirmed")
    if not auth_service.verify_password(body.password, user.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password")
    # Generate JWT
    access_token = await auth_service.create_access_token(data={"sub": user.email})
    refresh_token = await auth_service.create_refresh_token(data={"sub": user.email})
    await repositories_users.update_token(user, refresh_token, db)
    return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}

@router.get('/refresh_token',  response_model=TokenSchema)
async def refresh_token(credentials: HTTPAuthorizationCredentials = Depends(get_refresh_token),
                        db: AsyncSession = Depends(get_db)):
    """
    Refresh access and refresh tokens using a valid refresh token.

    :param credentials: Bearer token credentials.
    :param db: Database session.
    :return: New access and refresh tokens.
    :raises HTTPException: If token is invalid.
    """
    token = credentials.credentials
    email = await auth_service.decode_refresh_token(token)
    user = await repositories_users.get_user_by_email(email, db)
    if user.refresh_token != token:
        await repositories_users.update_token(user, None, db)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    access_token = await auth_service.create_access_token(data={"sub": email})
    refresh_token = await auth_service.create_refresh_token(data={"sub": email})
    await repositories_users.update_token(user, refresh_token, db)
    return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}

@router.get('/confirmed_email/{token}')
async def confirmed_email(token: str, db: AsyncSession = Depends(get_db)):
    """
    Confirm a user's email via token.

    :param token: Email confirmation token.
    :param db: Database session.
    :return: Confirmation message.
    :raises HTTPException: If token is invalid.
    """
    email = await auth_service.get_email_from_token(token)
    user = await repositories_users.get_user_by_email(email, db)
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verification error")
    if user.confirmed:
        return {"message": "Your email is already confirmed"}
    await repositories_users.confirmed_email(email, db)
    return {"message": "Email confirmed"}

@router.post('/request_email')
async def request_email(body: RequestEmail, background_tasks: BackgroundTasks, request: Request,
                        db: AsyncSession = Depends(get_db)):
    """
    Send a confirmation email if the email is not confirmed yet.

    :param body: Email input.
    :param background_tasks: Background task manager.
    :param request: HTTP request object.
    :param db: Database session.
    :return: Status message.
    """
    user = await repositories_users.get_user_by_email(body.email, db)

    if user.confirmed:
        return {"message": "Your email is already confirmed"}
    if user:
        background_tasks.add_task(send_email, user.email, user.username, str(request.base_url))
    return {"message": "Check your email for confirmation."}

@router.post('/request_reset_password')
async def request_reset_email(body: RequestEmail, background_tasks: BackgroundTasks, request: Request,
                        db: AsyncSession = Depends(get_db)):
    """
    Send a password reset email.

    :param body: Email input.
    :param background_tasks: Background task manager.
    :param request: HTTP request object.
    :param db: Database session.
    :return: Status message.
    """
    user = await repositories_users.get_user_by_email(body.email, db)

    if not user:
        return {"message": f"No user with email {body.email}"}
    if user:
        background_tasks.add_task(send_rp_email, user.email, user.username, str(request.base_url))
    return {"message": "Check your email for reset password."}

@router.get('/reset_password_form/{token}')
async def reset_password_form(request: Request, token: str):
    """
    Render the HTML form for password reset.

    :param request: HTTP request object.
    :param token: Password reset token.
    :return: HTML form template.
    """
    return templates.TemplateResponse("reset_password_form.html", {"request": request, "token": token})

@router.post('/reset_password/{token}')
async def reset_password(
    request: Request,
    token: str,
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Process the password reset.

    :param request: HTTP request object.
    :param token: Password reset token.
    :param new_password: New password.
    :param confirm_password: Password confirmation.
    :param db: Database session.
    :return: Success or error message.
    """
    if new_password != confirm_password:

        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": token, "error": "Passwords do not match"}
        )

    email = await auth_service.get_email_from_token(token)
    user = await repositories_users.get_user_by_email(email, db)
    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token")
    hash_password = auth_service.get_password_hash(new_password)
    await repositories_users.update_user_password(user, hash_password, db)
    return {"message": "Password updated."}
