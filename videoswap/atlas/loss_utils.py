import torch


# calculating the gradient loss as defined by Eq.7 in the paper
def get_gradient_loss(video_frames_dx, video_frames_dy, jif_current,
                      FG_UV_Mapping, BG_UV_Mapping, F_Atlas, F_Alpha,
                      rgb_output_foreground, norm_Scoord_func, norm_Tcoord_func, device):
    xplus1yt_foreground = torch.cat(
        [norm_Scoord_func(jif_current[0, :] + 1), norm_Scoord_func(jif_current[1, :]), norm_Tcoord_func(jif_current[2, :])], dim=1
    ).to(device)

    xyplus1t_foreground = torch.cat(
        [norm_Scoord_func(jif_current[0, :]), norm_Scoord_func(jif_current[1, :] + 1), norm_Tcoord_func(jif_current[2, :])], dim=1
    ).to(device)

    alphaxplus1 = 0.5 * (F_Alpha(xplus1yt_foreground) + 1.0)
    alphaxplus1 = alphaxplus1 * 0.99
    alphaxplus1 = alphaxplus1 + 0.001

    alphayplus1 = 0.5 * (F_Alpha(xyplus1t_foreground) + 1.0)
    alphayplus1 = alphayplus1 * 0.99
    alphayplus1 = alphayplus1 + 0.001

    # precomputed discrete derivative with respect to x,y direction
    rgb_dx_gt = video_frames_dx[jif_current[1, :], jif_current[0, :], :, jif_current[2, :]].squeeze(1).to(device)
    rgb_dy_gt = video_frames_dy[jif_current[1, :], jif_current[0, :], :, jif_current[2, :]].squeeze(1).to(device)

    # uv coordinates for locations with offsets of 1 pixel
    uv_foreground2_xyplus1t = BG_UV_Mapping(xyplus1t_foreground)
    uv_foreground2_xplus1yt = BG_UV_Mapping(xplus1yt_foreground)
    uv_foreground1_xyplus1t = FG_UV_Mapping(xyplus1t_foreground)
    uv_foreground1_xplus1yt = FG_UV_Mapping(xplus1yt_foreground)

    # The RGB values (from the 2 layers) for locations with offsets of 1 pixel
    rgb_output1_xyplus1t = (F_Atlas(uv_foreground1_xyplus1t * 0.5 + 0.5) + 1.0) * 0.5
    rgb_output1_xplus1yt = (F_Atlas(uv_foreground1_xplus1yt * 0.5 + 0.5) + 1.0) * 0.5
    rgb_output2_xyplus1t = (F_Atlas(uv_foreground2_xyplus1t * 0.5 - 0.5) + 1.0) * 0.5
    rgb_output2_xplus1yt = (F_Atlas(uv_foreground2_xplus1yt * 0.5 - 0.5) + 1.0) * 0.5

    # Reconstructed RGB values:
    rgb_output_foreground_xyplus1t = rgb_output1_xyplus1t * alphayplus1 + rgb_output2_xyplus1t * (1.0 - alphayplus1)
    rgb_output_foreground_xplus1yt = rgb_output1_xplus1yt * alphaxplus1 + rgb_output2_xplus1yt * (1.0 - alphaxplus1)

    # Use reconstructed RGB values for computing derivatives:
    rgb_dx_output = rgb_output_foreground_xplus1yt - rgb_output_foreground
    rgb_dy_output = rgb_output_foreground_xyplus1t - rgb_output_foreground
    gradient_loss = torch.mean((rgb_dx_gt - rgb_dx_output).norm(dim=1) ** 2 + (rgb_dy_gt - rgb_dy_output).norm(dim=1) ** 2)
    return gradient_loss


# get rigidity loss as defined in Eq. 9 in the paper
def get_rigidity_loss(
    jif_foreground, derivative_amount, larger_dim,
    UV_Mapping, uv_foreground, uv_mapping_scale,
    norm_Scoord_func, norm_Tcoord_func, device, return_all=False
):

    # concatenating (x,y-derivative_amount,t) and (x-derivative_amount,y,t) to get xyt_p:
    is_patch = norm_Scoord_func(torch.cat([jif_foreground[1, :] - derivative_amount, jif_foreground[1, :]]))
    js_patch = norm_Scoord_func(torch.cat([jif_foreground[0, :], jif_foreground[0, :] - derivative_amount]))
    fs_patch = norm_Tcoord_func(torch.cat([jif_foreground[2, :], jif_foreground[2, :]]))
    xyt_p = torch.cat((js_patch, is_patch, fs_patch), dim=1).to(device)

    uv_p = UV_Mapping(xyt_p)

    u_p = uv_p[:, 0].view(2, -1)  # u_p[0,:]= u(x,y-derivative_amount,t).  u_p[1,:]= u(x-derivative_amount,y,t)
    v_p = uv_p[:, 1].view(2, -1)  # v_p[0,:]= u(x,y-derivative_amount,t).  v_p[1,:]= v(x-derivative_amount,y,t)

    u_p_d_ = uv_foreground[:, 0].unsqueeze(0) - u_p  # u_p_d_[0,:]=u(x,y,t)-u(x,y-derivative_amount,t)   u_p_d_[1,:]= u(x,y,t)-u(x-derivative_amount,y,t).
    v_p_d_ = uv_foreground[:, 1].unsqueeze(0) - v_p  # v_p_d_[0,:]=u(x,y,t)-v(x,y-derivative_amount,t).  v_p_d_[1,:]= u(x,y,t)-v(x-derivative_amount,y,t).

    # to match units: 1 in uv coordinates is resx/2 in image space.
    du_dx = u_p_d_[1, :] * larger_dim / 2
    du_dy = u_p_d_[0, :] * larger_dim / 2
    dv_dy = v_p_d_[0, :] * larger_dim / 2
    dv_dx = v_p_d_[1, :] * larger_dim / 2

    jacobians = torch.cat((torch.cat((du_dx.unsqueeze(-1).unsqueeze(-1), du_dy.unsqueeze(-1).unsqueeze(-1)), dim=2),
                           torch.cat((dv_dx.unsqueeze(-1).unsqueeze(-1), dv_dy.unsqueeze(-1).unsqueeze(-1)), dim=2)), dim=1)
    jacobians = jacobians / uv_mapping_scale
    jacobians = jacobians / derivative_amount

    # jacobians = torch.nan_to_num(jacobians, nan=0.0, posinf=0.0, neginf=0.0)
    assert not torch.any(torch.isnan(jacobians)) and not torch.any(torch.isinf(jacobians))

    # Apply a loss to constrain the Jacobian to be a rotation matrix as much as possible
    JtJ = torch.matmul(jacobians.transpose(1, 2), jacobians)

    a = JtJ[:, 0, 0] + 0.001
    b = JtJ[:, 0, 1]
    c = JtJ[:, 1, 0]
    d = JtJ[:, 1, 1] + 0.001

    JTJinv = torch.zeros_like(jacobians).to(device)
    JTJinv[:, 0, 0] = d
    JTJinv[:, 0, 1] = -b
    JTJinv[:, 1, 0] = -c
    JTJinv[:, 1, 1] = a
    JTJinv = JTJinv / ((a * d - b * c).unsqueeze(-1).unsqueeze(-1))

    # JTJinv = torch.nan_to_num(JTJinv, nan=0.0, posinf=0.0, neginf=0.0)
    assert not torch.any(torch.isnan(JTJinv)) and not torch.any(torch.isinf(JTJinv))

    # See Equation (9) in the paper:
    rigidity_loss = (JtJ ** 2).sum(1).sum(1).sqrt() + (JTJinv ** 2).sum(1).sum(1).sqrt()

    assert not torch.any(torch.isnan(rigidity_loss)) and not torch.any(torch.isinf(rigidity_loss))

    if return_all:
        return rigidity_loss
    else:
        return rigidity_loss.mean()


# Compute optical flow loss (Eq. 11 in the paper) for all pixels without averaging. This is relevant for visualization of the loss.
def get_optical_flow_loss_all(jif_foreground, uv_foreground,
                              larger_dim, norm_Scoord_func, norm_Tcoord_func, model_F_mapping,
                              optical_flows, optical_flows_mask, uv_mapping_scale, device,
                              alpha=1.0):
    xyt_foreground_forward_should_match, relevant_batch_indices_forward = get_corresponding_flow_matches_all(
        jif_foreground, optical_flows_mask, optical_flows, norm_Scoord_func, norm_Tcoord_func)
    uv_foreground_forward_should_match = model_F_mapping(xyt_foreground_forward_should_match.to(device))

    errors = (uv_foreground_forward_should_match - uv_foreground).norm(dim=1)
    errors[relevant_batch_indices_forward == False] = 0  # noqa
    errors = errors * (alpha.squeeze())

    return errors * larger_dim / (2 * uv_mapping_scale)


# Compute optical flow loss (Eq. 11 in the paper)
def get_optical_flow_loss(jif_foreground, uv_foreground, optical_flows_reverse, optical_flows_reverse_mask,
                          larger_dim, UV_Mapping, optical_flows, optical_flows_mask, uv_mapping_scale,
                          norm_Scoord_func, norm_Tcoord_func, device, use_alpha=False, alpha=1.0):
    # Forward flow:
    uv_foreground_forward_relevant, xyt_foreground_forward_should_match, relevant_batch_indices_forward = get_corresponding_flow_matches(
        jif_foreground, optical_flows_mask, optical_flows, norm_Scoord_func, norm_Tcoord_func, True, uv_foreground)
    uv_foreground_forward_should_match = UV_Mapping(xyt_foreground_forward_should_match.to(device))
    loss_flow_next = (uv_foreground_forward_should_match - uv_foreground_forward_relevant).norm(dim=1) * larger_dim / (2 * uv_mapping_scale)

    # Backward flow:
    uv_foreground_backward_relevant, xyt_foreground_backward_should_match, relevant_batch_indices_backward = get_corresponding_flow_matches(
        jif_foreground, optical_flows_reverse_mask, optical_flows_reverse, norm_Scoord_func, norm_Tcoord_func, False, uv_foreground)
    uv_foreground_backward_should_match = UV_Mapping(xyt_foreground_backward_should_match.to(device))
    loss_flow_prev = (uv_foreground_backward_should_match - uv_foreground_backward_relevant).norm(dim=1) * larger_dim / (2 * uv_mapping_scale)

    if use_alpha:
        flow_loss = (loss_flow_prev * alpha[relevant_batch_indices_backward].squeeze()).mean() * 0.5 \
            + (loss_flow_next * alpha[relevant_batch_indices_forward].squeeze()).mean() * 0.5
    else:
        flow_loss = (loss_flow_prev).mean() * 0.5 + (loss_flow_next).mean() * 0.5

    return flow_loss


# A helper function for get_optical_flow_loss to return matching points according to the optical flow
def get_corresponding_flow_matches(jif_foreground, optical_flows_mask, optical_flows, norm_Scoord_func, norm_Tcoord_func, is_forward, uv_foreground, use_uv=True):

    batch_forward_mask = torch.where(optical_flows_mask[jif_foreground[1, :].squeeze(), jif_foreground[0, :].squeeze(), jif_foreground[2, :].squeeze(), :])
    forward_frames_amount = 2 ** batch_forward_mask[1]
    relevant_batch_indices = batch_forward_mask[0]
    jif_foreground_forward_relevant = jif_foreground[:, relevant_batch_indices, 0]
    forward_flows_for_loss = optical_flows[
        jif_foreground_forward_relevant[1], jif_foreground_forward_relevant[0], :, jif_foreground_forward_relevant[2], batch_forward_mask[1]]

    if is_forward:
        jif_foreground_forward_should_match = torch.stack(
            (jif_foreground_forward_relevant[0] + forward_flows_for_loss[:, 0],
             jif_foreground_forward_relevant[1] + forward_flows_for_loss[:, 1],
             jif_foreground_forward_relevant[2] + forward_frames_amount))
    else:
        jif_foreground_forward_should_match = torch.stack(
            (jif_foreground_forward_relevant[0] + forward_flows_for_loss[:, 0],
             jif_foreground_forward_relevant[1] + forward_flows_for_loss[:, 1],
             jif_foreground_forward_relevant[2] - forward_frames_amount))

    xyt_foreground_forward_should_match = torch.stack([norm_Scoord_func(jif_foreground_forward_should_match[0]),
                                                       norm_Scoord_func(jif_foreground_forward_should_match[1]),
                                                       norm_Tcoord_func(jif_foreground_forward_should_match[2])]).T
    if use_uv:
        uv_foreground_forward_relevant = uv_foreground[batch_forward_mask[0]]
        return uv_foreground_forward_relevant, xyt_foreground_forward_should_match, relevant_batch_indices
    else:
        return xyt_foreground_forward_should_match, relevant_batch_indices


# A helper function for get_optical_flow_loss_all to return matching points according to the optical flow
def get_corresponding_flow_matches_all(jif_foreground, optical_flows_mask, optical_flows, norm_Scoord_func, norm_Tcoord_func, use_uv=True):
    jif_foreground_forward_relevant = jif_foreground

    forward_flows_for_loss = optical_flows[jif_foreground_forward_relevant[1], jif_foreground_forward_relevant[0], :, jif_foreground_forward_relevant[2], 0].squeeze()
    forward_flows_for_loss_mask = optical_flows_mask[jif_foreground_forward_relevant[1], jif_foreground_forward_relevant[0], jif_foreground_forward_relevant[2], 0].squeeze()

    jif_foreground_forward_should_match = torch.stack(
        (jif_foreground_forward_relevant[0].squeeze() + forward_flows_for_loss[:, 0],
         jif_foreground_forward_relevant[1].squeeze() + forward_flows_for_loss[:, 1],
         jif_foreground_forward_relevant[2].squeeze() + 1))

    xyt_foreground_forward_should_match = torch.stack([
        norm_Scoord_func(jif_foreground_forward_should_match[0]),
        norm_Scoord_func(jif_foreground_forward_should_match[1]),
        norm_Tcoord_func(jif_foreground_forward_should_match[2])
    ]).T

    if use_uv:
        return xyt_foreground_forward_should_match, forward_flows_for_loss_mask > 0
    else:
        return 0


# Compute alpha optical flow loss (Eq. 12 in the paper)
def get_optical_flow_alpha_loss(F_Alpha, jif_foreground, alpha, optical_flows_reverse, optical_flows_reverse_mask,
                                norm_Scoord_func, norm_Tcoord_func, optical_flows, optical_flows_mask, device):
    # Forward flow
    xyt_foreground_forward_should_match, relevant_batch_indices_forward = get_corresponding_flow_matches(
        jif_foreground, optical_flows_mask, optical_flows, norm_Scoord_func, norm_Tcoord_func, True, 0, use_uv=False)
    alpha_foreground_forward_should_match = F_Alpha(xyt_foreground_forward_should_match.to(device))
    alpha_foreground_forward_should_match = 0.5 * (alpha_foreground_forward_should_match + 1.0)
    alpha_foreground_forward_should_match = alpha_foreground_forward_should_match * 0.99
    alpha_foreground_forward_should_match = alpha_foreground_forward_should_match + 0.001
    loss_flow_alpha_next = (alpha[relevant_batch_indices_forward] - alpha_foreground_forward_should_match).abs().mean()

    # Backward loss
    xyt_foreground_backward_should_match, relevant_batch_indices_backward = get_corresponding_flow_matches(
        jif_foreground, optical_flows_reverse_mask, optical_flows_reverse, norm_Scoord_func, norm_Tcoord_func, False, 0,
        use_uv=False)
    alpha_foreground_backward_should_match = F_Alpha(xyt_foreground_backward_should_match.to(device))
    alpha_foreground_backward_should_match = 0.5 * (alpha_foreground_backward_should_match + 1.0)
    alpha_foreground_backward_should_match = alpha_foreground_backward_should_match * 0.99
    alpha_foreground_backward_should_match = alpha_foreground_backward_should_match + 0.001
    loss_flow_alpha_prev = (alpha_foreground_backward_should_match - alpha[relevant_batch_indices_backward]).abs().mean()

    return (loss_flow_alpha_next + loss_flow_alpha_prev) * 0.5


# Compute alpha optical flow loss (Eq. 12 in the paper) for all the pixels for visualization.
def get_optical_flow_alpha_loss_all(model_alpha, jif_foreground, alpha, norm_Scoord_func, norm_Tcoord_func, optical_flows, optical_flows_mask, device):
    xyt_foreground_forward_should_match, relevant_batch_indices_forward = get_corresponding_flow_matches_all(
        jif_foreground, optical_flows_mask, optical_flows, norm_Scoord_func, norm_Tcoord_func)
    alpha_foreground_forward_should_match = model_alpha(xyt_foreground_forward_should_match.to(device))
    alpha_foreground_forward_should_match = 0.5 * (alpha_foreground_forward_should_match + 1.0)
    alpha_foreground_forward_should_match = alpha_foreground_forward_should_match * 0.99
    alpha_foreground_forward_should_match = alpha_foreground_forward_should_match + 0.001

    loss_flow_alpha_next = (alpha - alpha_foreground_forward_should_match).abs()
    loss_flow_alpha_next[relevant_batch_indices_forward == False] = 0  # noqa

    return loss_flow_alpha_next
