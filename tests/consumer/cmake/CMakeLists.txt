# A find_package(Pdfium) consumer: checks the CMake package files libpdfium-dev ships for
# compatibility with the legacy package (target Pdfium::pdfium). CMake is only used here, to
# test the installed files; PDFium itself is never built with CMake.
cmake_minimum_required(VERSION 3.16)
project(pdfium_cmake_consumer C)
find_package(Pdfium 155 REQUIRED)
add_executable(pdfium_cmake_consumer ../pdfium_probe.c)
target_link_libraries(pdfium_cmake_consumer PRIVATE Pdfium::pdfium)
