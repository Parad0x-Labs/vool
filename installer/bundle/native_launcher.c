/* LaunchServices needs a Mach-O executable with the bundle's actual CPU slice. */
#include <mach-o/dyld.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    uint32_t size = 0;
    _NSGetExecutablePath(NULL, &size);
    char *image = calloc((size_t)size + 1, 1);
    if (!image || _NSGetExecutablePath(image, &size) != 0) {
        fprintf(stderr, "VOOL: cannot resolve its launcher\n");
        return 1;
    }
    char resolved[PATH_MAX], script[PATH_MAX];
    if (!realpath(image, resolved)) {
        perror("VOOL launcher path");
        return 1;
    }
    free(image);
    char *leaf = strrchr(resolved, '/');
    if (!leaf) return 1;
    *leaf = '\0';
    int length = snprintf(script, sizeof(script), "%s/../Resources/launcher.sh", resolved);
    if (length < 0 || (size_t)length >= sizeof(script) || access(script, R_OK) != 0) {
        fprintf(stderr, "VOOL: its bundled launch script is missing\n");
        return 1;
    }
    char **arguments = calloc((size_t)argc + 2, sizeof(char *));
    if (!arguments) return 1;
    arguments[0] = "/bin/bash";
    arguments[1] = script;
    for (int i = 1; i < argc; ++i) arguments[i + 1] = argv[i];
    execv(arguments[0], arguments);
    perror("VOOL launcher exec");
    return 1;
}
