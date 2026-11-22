QCC    ?= qcc
CFLAGS ?= -Vgcc_ntox86_64 -O2 -Wall

build/controller: src/controller.c
	@mkdir -p build
	$(QCC) $(CFLAGS) -I$(REFEREE_INCLUDE) -o $@ $<

clean:
	rm -rf build

.PHONY: clean
