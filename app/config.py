from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    MYSQL_HOST: str = "mysql"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "ecom_user"
    MYSQL_PASSWORD: str = "ecom_pass"
    MYSQL_DATABASE: str = "ecommerce"
    ES_HOST: str = "http://elasticsearch:9200"
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    APP_PORT: int = 8000

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"
        )

    class Config:
        env_file = ".env"


settings = Settings()
