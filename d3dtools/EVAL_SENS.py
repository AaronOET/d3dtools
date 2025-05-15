#%% import pandas as pd
import geopandas as gpd
import os
import numpy as np
import matplotlib.pyplot as plt

#%% Read SIM.shp and OBS_SENSOR.shp from SHP directory
shp_dir = "SHP"
sim_fp = os.path.join(shp_dir, "SIM.shp")
obs_fp = os.path.join(shp_dir, "OBS_SENSOR.shp")

sim_gdf = gpd.read_file(sim_fp)
obs_gdf = gpd.read_file(obs_fp)

# %% Create a new layer which is buffer of obs_gdf with radius of 30
obs_buffer = obs_gdf.copy()
obs_buffer['geometry'] = obs_buffer.geometry.buffer(30)

# %% Save the buffer to a new shapefile
obs_buffer.to_file(os.path.join(shp_dir, "OBS_BUFFER.shp"))
# %% Count the number of obs_buffer with attribute '最大深' >= 30 that are intersecting with sim_gdf (aka True Positive)
True_Positive = 0
for _, obs in obs_buffer.iterrows():
    if obs['最大深'] >= 30:
        # print('hello')
        if sim_gdf.intersects(obs.geometry).any():
            True_Positive += 1
# %% Calculate False Negative
False_Negative = len(obs_buffer[obs_buffer['最大深'] >= 30]) - True_Positive

# %% Count the number of obs_buffer with attribute '最大深' < 30 that are intersecting with sim_gdf (aka False Positive)
False_Positive = 0
for _, obs in obs_buffer.iterrows():
    if obs['最大深'] < 30:
        if sim_gdf.intersects(obs.geometry).any():
            False_Positive += 1

# %% Calculate True Negative
True_Negative = len(obs_buffer) - (True_Positive + False_Negative +
                                   False_Positive)

# %% Print the counts
print(f"True Positive: {True_Positive}")
print(f"True Negative: {True_Negative}")
print(f"False Positive: {False_Positive}")
print(f"False Negative: {False_Negative}")

#%% Calculate the accuracy and recall
accuracy = (True_Positive + True_Negative) / len(obs_buffer)
recall = True_Positive / (True_Positive + False_Negative)
# %% Print the accuracy and recall
print(f"Accuracy: {accuracy*100:.2f}%")
print(f"Recall: {recall*100:.2f}%")

# %%
