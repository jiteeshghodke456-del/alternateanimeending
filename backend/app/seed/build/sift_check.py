import cv2, numpy as np, sys, os
def inliers(logo_path, scene_path, ratio=0.75):
    logo = cv2.imread(logo_path, cv2.IMREAD_GRAYSCALE); scene = cv2.imread(scene_path, cv2.IMREAD_GRAYSCALE)
    sift = cv2.SIFT_create()
    k1, d1 = sift.detectAndCompute(logo, None); k2, d2 = sift.detectAndCompute(scene, None)
    if d1 is None or d2 is None: return 0
    good = [m for m, n in cv2.BFMatcher().knnMatch(d1, d2, k=2) if m.distance < ratio * n.distance]
    if len(good) < 4: return 0
    src = np.float32([k1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2); dst = np.float32([k2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    return int(mask.sum()) if mask is not None else 0
if __name__ == "__main__":
    seed = sys.argv[1]
    for scene in ("ad_good", "ad_offpalette", "ad_wronglogo"):
        for lg in ("logo.png", "logo_alt_wrong.png"):
            print(scene, lg, inliers(os.path.join(seed, lg), os.path.join(seed, "content", scene + ".png")))
