# Z-Assistant
Z by HP Intelligent Conversational Assistant that helps in selecting the right product

## Run the application

### 1. Setup display

```sh
export DISPLAY=:0
xhost +
```

### 2. Run Docker Compose

```sh
docker compose up --build --remove-orphans
```

## Notes

- Set `BOOST` as `'1'` in `docker-compose.yml` to use Boost
- Load up time with boost can be long, please be patient. 
- Use `/promptquality` to run prompt quality anytime between a conversation

