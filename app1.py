import google.generativeai as genai
import os

genai.configure(api_key='AIzaSyA4CFON9Zk_yaAE-AIOzHKNKvAFhpou0XI')

print("Listing available models...")
for m in genai.list_models():
    if 'generateContent' in m.supported_generation_methods:
        print(m.name)