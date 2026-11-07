from .gpu import add_cuda_dlls

add_cuda_dlls()          # before Whisper/Kokoro load, so they can find the CUDA libraries
