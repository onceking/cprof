all: test

test:
	docker build -t "cprof:latest" .
	docker run -t -i --rm \
		-v ${HOME}/cprof/:/app/ \
		 "cprof:latest"
