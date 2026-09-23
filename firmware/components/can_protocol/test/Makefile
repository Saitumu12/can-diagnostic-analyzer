CC ?= cc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -Werror -Wconversion -Wshadow -Wpedantic
INCLUDES := -I../include
SOURCES := ../can_protocol.c protocol_vectors.c
TARGET := protocol_vectors

.PHONY: all run clean

all: $(TARGET)

$(TARGET): $(SOURCES) ../include/can_protocol.h
	$(CC) $(CFLAGS) $(INCLUDES) $(SOURCES) -o $@

run: $(TARGET)
	./$(TARGET)

clean:
	rm -f $(TARGET) $(TARGET).exe
