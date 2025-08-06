"""OAuth 2.1 authentication for remote MCP server."""
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

# OAuth configuration
OAUTH_SECRET_KEY = secrets.token_urlsafe(32)  # In production, use environment variable
OAUTH_ALGORITHM = "HS256"
OAUTH_ACCESS_TOKEN_EXPIRE_MINUTES = 30
OAUTH_REFRESH_TOKEN_EXPIRE_DAYS = 7

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()


class TokenData(BaseModel):
    """Token data model."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class User(BaseModel):
    """User model for OAuth."""
    id: str
    email: str
    name: str


# Simple in-memory user store (replace with database in production)
USERS_DB = {
    "user@example.com": {
        "id": "user-1",
        "email": "user@example.com", 
        "name": "Example User",
        "hashed_password": pwd_context.hash("password123"),  # Replace with proper auth
    }
}


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password."""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: timedelta | None = None):
    """Create JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, OAUTH_SECRET_KEY, algorithm=OAUTH_ALGORITHM)


def create_refresh_token(data: dict):
    """Create JWT refresh token."""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=OAUTH_REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, OAUTH_SECRET_KEY, algorithm=OAUTH_ALGORITHM)


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> User:
    """Get current authenticated user."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = jwt.decode(credentials.credentials, OAUTH_SECRET_KEY, algorithms=[OAUTH_ALGORITHM])
        user_id: str = payload.get("sub")
        token_type: str = payload.get("type")
        
        if user_id is None or token_type != "access":
            raise credentials_exception
            
    except JWTError:
        raise credentials_exception
    
    # Find user in database
    for user_data in USERS_DB.values():
        if user_data["id"] == user_id:
            return User(**user_data)
    
    raise credentials_exception


def authenticate_user(email: str, password: str) -> dict | None:
    """Authenticate user credentials."""
    user = USERS_DB.get(email)
    if not user or not verify_password(password, user["hashed_password"]):
        return None
    return user