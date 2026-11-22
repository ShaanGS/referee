QCC    ?= qcc
CFLAGS ?= -Vgcc_ntox86_64 -O2 -Wall

build/controller: src/controller.c
	@mkdir -p build
	$(QCC) $(CFLAGS) -I$(REFEREE_INCLUDE) -o $@ $<
	@sh tools/escape.sh

clean:
	rm -rf build

.PHONY: clean
