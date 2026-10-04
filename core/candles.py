import pandas as pd
from datetime import timedelta

def feed(m15_df : pd.DataFrame,
          h1_df : pd.DataFrame, 
          h4_df : pd.DataFrame, 
          d1_df : pd.DataFrame , 
          w1_df : pd.DataFrame,
          ) : 
    m15_dflist = list(m15_df.itertuples(index=False))
    h1_dflist = list(h1_df.itertuples(index=False))
    h4_dflist = list(h4_df.itertuples(index=False))
    d1_dflist = list(d1_df.itertuples(index=False))
    w1_dflist = list(w1_df.itertuples(index=False))

    h1_pos = 0
    h4_pos = 0
    d1_pos = 0
    w1_pos = 0

    for m15 in m15_dflist :
        now = m15.time + timedelta(minutes=15)
        data_returned = {
            "m15" : [m15],
        }
        while h1_pos < len(h1_dflist) and h1_dflist[h1_pos].time + timedelta(hours=1)<= now :
            data_returned.setdefault("h1", []).append(h1_dflist[h1_pos])
            h1_pos += 1
        while h4_pos < len(h4_dflist) and h4_dflist[h4_pos].time + timedelta(hours=4)<= now :
            data_returned.setdefault("h4", []).append(h4_dflist[h4_pos])
            h4_pos += 1
        while d1_pos < len(d1_dflist) and d1_dflist[d1_pos].time + timedelta(days=1)<= now :
            data_returned.setdefault("d1", []).append(d1_dflist[d1_pos])
            d1_pos += 1
        while w1_pos < len(w1_dflist) and w1_dflist[w1_pos].time + timedelta(weeks=1)<= now :
            data_returned.setdefault("w1", []).append(w1_dflist[w1_pos])
            w1_pos += 1
        yield now , data_returned