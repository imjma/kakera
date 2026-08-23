.PHONY: restart

restart:
	cd deploy/synology && sudo docker compose --profile workers stop \
		&& sudo docker compose --profile workers build \
		&& sudo docker compose --profile workers up -d
